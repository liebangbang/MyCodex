# -*- coding: utf-8 -*-
"""
MyCodex 后端（pywebview 版）——接入 DeepSeek API，承接编码代理逻辑。

本文件只负责「大脑」：流式对话、工具执行、会话存档、思考档判定。
所有界面交互通过 call_js() 把数据推给前端 HTML/JS。
"""

import os
import sys
import json
import re
import time
import queue
import signal
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import socket

import webview

# --------------------------------------------------------------------------
# 启动日志：排查 GUI 启动问题；落盘到 ~/.config/mycode/startup.log
# --------------------------------------------------------------------------
_LOGFILE = str(Path.home() / ".config" / "mycode" / "startup.log")
try:
    os.makedirs(os.path.dirname(_LOGFILE), exist_ok=True)
    open(_LOGFILE, "w", encoding="utf-8").close()
except Exception:
    pass


def log(msg):
    line = "%.3f %s" % (time.time(), msg)
    try:
        with open(_LOGFILE, "a", encoding="utf-8") as _f:
            _f.write(line + "\n")
            _f.flush()
    except Exception:
        pass
    print(msg, file=sys.stderr, flush=True)


def log_error(exc, where="", ctx=None):
    """把异常完整留档到 error.log（界面只提示一句话，traceback 与上下文写盘），
    避免每次故障只能靠用户复述弹窗文字。ctx 为 agent 线程上下文时附带任务与消息量。"""
    try:
        import traceback
        tb = traceback.format_exc()
        info = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "where": where,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "traceback": tb,
        }
        if ctx:
            info["task"] = ctx.get("task")
            info["history_len"] = len(ctx.get("history") or [])
            info["transcript_len"] = len(ctx.get("transcript") or [])
        line = json.dumps(info, ensure_ascii=False)
        try:
            ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ERROR_LOG, "a", encoding="utf-8") as _f:
                _f.write(line + "\n---\n")
        except Exception:
            pass
    except Exception:
        pass
    log("ERROR[%s] %s: %s" % (where, type(exc).__name__, exc))


# --------------------------------------------------------------------------
# 配置与常量
# --------------------------------------------------------------------------
APP_NAME = "MyCodex"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
CONFIG_DIR = Path.home() / ".config" / "mycode"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_DIR = CONFIG_DIR / "sessions"
ERROR_LOG = CONFIG_DIR / "error.log"   # 异常留档：界面只提示一句话，详情写这里供排查

RISKY_PATTERNS = [
    r"\brm\s+-rf\b", r"\brm\s+-r\b", r"\brm\s+-fr\b",
    r"\bsudo\b", r"\bmkfs\b", r"\bdd\b\s+if=",
    r"\bgit\s+push\s+--force", r"\bgit\s+push\s+-f\b",
    r"\bgit\s+reset\s+--hard", r"\bgit\s+checkout\s+--\s",
    r"curl\b[^\n|]*\|\s*(sh|bash)", r"wget\b[^\n|]*\|\s*(sh|bash)",
    r"\bchmod\s+-R\b", r">\s*/dev/sd", r"\bshutdown\b", r"\breboot\b",
    r":\(\)\s*\{\s*:", r"\bmv\b\s+/",
]

CMD_OUTPUT_LIMIT = 40000
READ_LINE_LIMIT = 2000
MAX_REPEAT = 3  # 连续相同工具调用超过此值视为死循环，自动停止
TOOL_EXEC_TIMEOUT = 60  # 文件类工具单次执行超时（秒），防读超大文件/扫巨型目录挂死生成线程
CMD_EXEC_TIMEOUT = 120  # run_command 单次执行超时（秒）：超时后杀整个进程组并保留已产生的输出
# 注：对话轮数已设为不限（_agent_loop 用 while True），靠 MAX_REPEAT 防死循环；
#     如仍需硬性轮数上限，可改回 `for _ in range(N)` 并定义 MAX_TURNS = N。
MAX_TOKENS = 8192
MAX_CONTINUE = 5           # 长任务输出超 max_tokens 时，自动续写的最大段数（防无限循环）
TRANSCRIPT_HARD_LIMIT = 1_500_000  # 会话落盘硬上限（~1.5MB），超出丢弃最旧段，防文件膨胀卡死
VISION_MAX_TOKENS = 4096   # 千问 VL 单次回复上限（比 DeepSeek 保守）
VISION_MAX_PDF_PAGES = 5   # PDF 最多转 5 页发给视觉模型

# 上下文管理：防止历史无限膨胀导致请求超时/中断
CTX_HARD_LIMIT = 150        # 发给模型的 history 消息数硬上限（超出则压缩）
CTX_SOFT_LIMIT = 400_000    # 发给模型的 history 总字符软上限（超出则压缩）
CTX_KEEP_RECENT = 40        # 压缩时保留最近 N 条完整消息（含本轮 user）
CTX_HARD_BYTES = 800_000    # 请求体序列化字节硬上限（发送前超出则强制压缩，防 API 超限/超时中断）
MAX_MSG_CHARS = 120_000     # 单条消息字符上限（发送前截断，防超大粘贴/日志导致请求超限）
PLAYBACK_LIMIT = 80         # 启动/打开任务时，界面最多回放的历史消息条数（防卡顿）# 网络重试：连接建立阶段可重试（未开始流式输出时）；进入流式后不重试避免重复内容
API_RETRY = 3               # 最大重试次数（指数退避 1/2/4s）
API_RETRY_BASE_DELAY = 1.0
STREAM_READ_TIMEOUT = 2.0   # 流式读取单次 socket 超时（秒）：期间可及时响应「停止」
API_CONNECT_TIMEOUT = 30     # 连接建立阶段超时（秒）：provider 端网络挂死时避免 UI 冻结长达 600s
STREAM_IDLE_LIMIT = 600.0   # 流式长时间无数据（秒）视为断流，保持原有 600s 超时语义

COMPLEX_KEYWORDS = [
    "设计", "重构", "架构", "优化", "调试", "排错", "分析", "为什么", "原理", "比较",
    "权衡", "修复", "排查", "定位", "方案", "实现", "解释", "review", "总结",
    "refactor", "debug", "analyze", "design", "architect", "optimize", "why",
    "compare", "review", "implement", "how", "对比", "评估", "取舍",
]

# 产物预览字节上限
PREVIEW_LIMIT = 60000


# --------------------------------------------------------------------------
# 工具定义
# --------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件内容。可指定起始行与行数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件相对或绝对路径"},
                    "offset": {"type": "integer", "description": "起始行（从 1 开始），默认 1"},
                    "limit": {"type": "integer", "description": f"读取行数，默认 {READ_LINE_LIMIT}"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖写入整个文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "把 old_string 替换为 new_string（要求 old_string 唯一）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "old_string": {"type": "string", "description": "要被替换的原文（必须唯一）"},
                    "new_string": {"type": "string", "description": "替换后的新内容"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认当前目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "在文件中按正则搜索内容，返回匹配行与行号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "搜索根目录，默认当前目录"},
                    "glob": {"type": "string", "description": "文件过滤，如 '*.py'，默认所有文本文件"},
                    "max_matches": {"type": "integer", "description": "最大返回匹配数，默认 50"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在终端执行 shell 命令（工作目录内）。用于构建、测试、git 等操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                },
                "required": ["command"],
            },
        },
    },
]


# --------------------------------------------------------------------------
# 思考模式
# --------------------------------------------------------------------------
def think_auto(user_input):
    t = user_input.lower()
    score = 0
    for kw in COMPLEX_KEYWORDS:
        if kw in t:
            score += 1
    if len(user_input) > 120:
        score += 1
    if "```" in user_input:
        score += 1
    if user_input.count("文件") >= 2 or t.count("file") >= 2:
        score += 1
    return score >= 2


def resolve_think(mode, user_input):
    if mode == "on":
        return True
    if mode == "off":
        return False
    return think_auto(user_input)


# --------------------------------------------------------------------------
# 配置加载
# --------------------------------------------------------------------------
def load_config():
    cfg = {
        "api_key": os.environ.get("DEEPSEEK_API_KEY"),
        "model": DEFAULT_MODEL,
        "base_url": DEFAULT_BASE_URL,
        "vision_provider": os.environ.get("VISION_PROVIDER") or "qwen",
        "vision_api_key": os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("VISION_API_KEY"),
        "vision_model": os.environ.get("QWEN_VL_MODEL") or os.environ.get("VISION_MODEL"),
        "vision_base_url": os.environ.get("VISION_BASE_URL"),
    }
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for k in ("api_key", "model", "base_url", "vision_provider",
                      "vision_api_key", "vision_model", "vision_base_url"):
                if data.get(k):
                    cfg[k] = data[k]
        except Exception as e:
            log("读取配置文件失败：" + str(e))
    return cfg


# 各视觉厂商默认 endpoint（OpenAI 兼容多模态接口）
_VISION_DEFAULTS = {
    "qwen": {
        "model": "qwen-vl-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "openai": {
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
    },
    "glm": {
        "model": "glm-4v-plus",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
}


def _vision_cfg(cfg):
    """返回多模态调用所需的配置；支持 provider 自动选择 endpoint。
    key 未单独配置时回退用主 key（多数视觉厂商与 DeepSeek 不通，仅作兜底）。"""
    provider = (cfg.get("vision_provider") or "qwen").lower()
    d = _VISION_DEFAULTS.get(provider, _VISION_DEFAULTS["qwen"])
    return {
        "provider": provider,
        "api_key": cfg.get("vision_api_key") or cfg.get("api_key"),
        "model": cfg.get("vision_model") or d["model"],
        "base_url": cfg.get("vision_base_url") or d["base_url"],
    }


# 主模型（通义/Qwen）不可用时，自动切换到 DeepSeek 续跑同一轮对话
_FALLBACK_MODEL = "deepseek-v4-flash"
_FATAL_HINTS = ("arrearage", "overdue", "insufficient", "balance",
                "401", "403", "authentication", "access denied",
                "invalid api key", "unauthorized")


def _is_fatal_provider_err(e):
    """判断是否为「账户/鉴权类」致命错误（非瞬时，值得切备用 provider 而非重试）。"""
    msg = str(e).lower()
    return any(h in msg for h in _FATAL_HINTS)


def _fallback_available(cfg):
    """仅当主模型是 Qwen（通义）且配置了 DeepSeek key 时，DeepSeek 才作为备用。"""
    return bool(cfg.get("api_key")) and (cfg.get("model") or "").lower().startswith("qwen")


def _friendly_err(e):
    """把底层错误转成用户可操作的提示。"""
    s = str(e)
    low = s.lower()
    if "arrearage" in low or "overdue" in low or "insufficient" in low or "balance" in low:
        return ("阿里云（通义）账户欠费或额度不足，对话已停止。"
                "请结清欠费，或把模型切到「深度·Flash / 深度·Pro」(DeepSeek) 继续。")
    if "401" in s or "403" in s or "authentication" in low or "access denied" in low or "invalid api key" in low:
        return "API Key 鉴权失败，请检查 config.json 的 api_key / vision_api_key。"
    return s


def _chat_cfg(cfg, model=None):
    """根据模型名自动选 endpoint：DeepSeek（自有）/ Qwen（复用 vision_api_key 走 DashScope 兼容模式）"""
    model = model or cfg.get("model", DEFAULT_MODEL)
    if model and model.lower().startswith("qwen"):
        return {
            "provider": "qwen",
            "api_key": cfg.get("vision_api_key") or cfg.get("api_key"),
            "base_url": cfg.get("vision_base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": model,
        }
    return {
        "provider": "deepseek",
        "api_key": cfg.get("api_key"),
        "base_url": cfg.get("base_url", DEFAULT_BASE_URL),
        "model": model,
    }


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------
def _resolve(path, cwd):
    p = Path(path)
    if not p.is_absolute():
        p = (cwd / p).resolve()
    return p


def _truncate(s, limit):
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... (输出已截断，共 {len(s)} 字符)"


def tool_read_file(args, cwd):
    try:
        p = _resolve(args["path"], cwd)
        if not p.exists():
            return f"错误：文件不存在：{p}"
        if p.is_dir():
            return f"错误：{p} 是目录，请用 list_dir"
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", READ_LINE_LIMIT))
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        seg = lines[offset - 1: offset - 1 + limit]
        body = "\n".join(f"{i + offset:>6}\t{ln}" for i, ln in enumerate(seg))
        more = f"\n（显示第 {offset}-{offset + len(seg) - 1} 行 / 共 {total} 行）" if total > offset + len(seg) - 1 else ""
        return f"=== {p} ===\n{body}{more}"
    except Exception as e:
        return f"错误：{e}"


def tool_write_file(args, cwd):
    try:
        p = _resolve(args["path"], cwd)
        content = args.get("content", "")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        nlines = content.count("\n") + 1 if content else 0
        return f"已写入 {p}（{len(content)} 字符，{nlines} 行）"
    except Exception as e:
        return f"错误：{e}"


def tool_edit_file(args, cwd):
    try:
        p = _resolve(args["path"], cwd)
        if not p.exists():
            return f"错误：文件不存在：{p}"
        text = p.read_text(encoding="utf-8", errors="replace")
        old = args["old_string"]
        new = args["new_string"]
        cnt = text.count(old)
        if cnt == 0:
            return "错误：未找到 old_string"
        if cnt > 1:
            return f"错误：old_string 出现 {cnt} 次，不唯一，请提供更多上下文"
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"已修改 {p}（1 处替换）"
    except Exception as e:
        return f"错误：{e}"


def tool_list_dir(args, cwd):
    try:
        p = _resolve(args.get("path", "."), cwd)
        if not p.exists():
            return f"错误：目录不存在：{p}"
        items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        lines = []
        for it in items:
            tag = "[dir]" if it.is_dir() else "[file]"
            try:
                sz = f"{it.stat().st_size}B" if it.is_file() else ""
            except Exception:
                sz = ""
            lines.append(f"  {tag} {it.name}  {sz}")
        return f"=== {p} ===\n" + ("\n".join(lines) if lines else "(空目录)")
    except Exception as e:
        return f"错误：{e}"


def tool_grep_files(args, cwd):
    try:
        pattern = args["pattern"]
        root = _resolve(args.get("path", "."), cwd)
        cwd_resolved = cwd.resolve()
        glob = args.get("glob", "*")
        max_matches = int(args.get("max_matches", 50))
        rx = re.compile(pattern)
        results = []
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".workbuddy"}
        for path in root.rglob(glob):
            if not path.is_file():
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    rel = os.path.relpath(path, cwd_resolved)
                    results.append(f"{rel}:{i}: {line.strip()}")
                    if len(results) >= max_matches:
                        break
            if len(results) >= max_matches:
                break
        if not results:
            return f"未匹配到：{pattern}"
        head = f"匹配 {len(results)} 条（pattern={pattern}）：\n"
        return head + "\n".join(results)
    except Exception as e:
        return f"错误：{e}"


def _is_risky(command):
    return any(re.search(p, command) for p in RISKY_PATTERNS)


def _kill_process_tree(proc):
    """杀掉整个进程组（含 shell 派生的子进程），防止超时后编译/测试进程残留继续占用资源。
    Popen 时用 start_new_session=True 让 shell 成为新进程组组长，这里按进程组号一次清干净。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def tool_run_command(args, cwd, approve_fn=None):
    command = args.get("command", "")
    risky = _is_risky(command)
    if risky:
        if approve_fn is None:
            return "[已拒绝] 该命令需要确认，但当前无法弹窗确认。命令未执行：\n" + command
        if not approve_fn(command):
            return "[已取消] 用户拒绝执行命令。"
    try:
        import subprocess
        # Popen + communicate 而非 run：超时后仍能拿到已产生的部分输出（run 会全部丢弃），
        # 让模型能看到"命令跑到哪一步"，而不是面对一片空白；start_new_session 隔离进程组，
        # 超时杀进程组而非只杀 shell，杜绝子进程残留导致的后续资源卡死。
        proc = subprocess.Popen(
            command, shell=True, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", start_new_session=True,
        )
        timed_out = False
        try:
            out, err = proc.communicate(timeout=CMD_EXEC_TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_tree(proc)
            # 进程组已被杀，再收割一次拿到缓冲区内已产生的输出
            out, err = proc.communicate()
        if timed_out:
            note = f"\n[命令执行超过 {CMD_EXEC_TIMEOUT}s 已终止（含其子进程），以下为超时前已产生的输出]"
        else:
            note = ""
        merged = (out or "") + (("\n--- stderr ---\n" + err) if err else "")
        merged = _truncate(merged, CMD_OUTPUT_LIMIT)
        return merged + note + f"\n[exit code: {proc.returncode}]"
    except subprocess.TimeoutExpired:
        return f"[超时] 命令执行超过 {CMD_EXEC_TIMEOUT} 秒被终止。"
    except Exception as e:
        return f"执行错误：{e}"


TOOL_IMPL = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "list_dir": tool_list_dir,
    "grep_files": tool_grep_files,
}


def _run_tool_with_timeout(name, args, cwd, timeout=TOOL_EXEC_TIMEOUT):
    """在独立线程中执行文件类工具并限时，防止读超大文件/扫巨型目录把生成线程永久挂住。
    run_command 不走这里（内部已有 subprocess 超时与用户确认等待，走自身逻辑）。
    超时后遗留的 daemon 线程自生自灭（读操作无副作用），主流程立即拿到错误提示交给模型。"""
    fn = TOOL_IMPL[name]
    q = queue.Queue(maxsize=1)

    def _worker():
        try:
            q.put(("ok", fn(args, cwd)))
        except Exception as e:
            q.put(("err", "%s" % e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "[超时] 工具 %s 执行超过 %d 秒被终止；请缩小读取范围或换用更精确的路径重试。" % (name, timeout)
    status, val = q.get()
    return val if status == "ok" else "执行错误：%s" % val


# --------------------------------------------------------------------------
# DeepSeek 流式调用
# --------------------------------------------------------------------------
def stream_chat(messages, cfg, tools, thinking, max_tokens, on_text=None, on_reason=None, should_stop=None, model=None):
    cc = _chat_cfg(cfg, model or cfg.get("model"))
    body = {
        "model": cc["model"],
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
        "max_tokens": max_tokens,
    }
    if thinking:
        if cc["provider"] == "qwen":
            # DashScope 兼容模式：enable_thinking 顶层布尔
            body["enable_thinking"] = True
        else:
            # DeepSeek：thinking 对象 + reasoning_effort
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "high"

    data = json.dumps(body).encode("utf-8")
    content = []
    reasoning = []
    tc_acc = {}
    finish_reason = None

    # 连接建立阶段可重试（HTTP 5xx / 网络错误 / 超时），进入流式读取后不再重试
    last_err = None
    for attempt in range(API_RETRY + 1):
        if attempt > 0:
            delay = API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            time.sleep(delay)
        req = Request(
            cc["base_url"].rstrip("/") + "/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cc['api_key']}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            resp = urlopen(req, timeout=API_CONNECT_TIMEOUT)
            break
        except HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:1200]
            except Exception:
                pass
            last_err = f"HTTP {e.code}: {e.reason}"
            if err_body:
                last_err += f"\n服务器返回：{err_body}"
            if e.code == 429:
                # 限流（高峰期 DeepSeek 常见）：按服务端 Retry-After 或指数退避重试，
                # 429 不是参数/鉴权问题，直接判死会让用户误以为"卡断/失败"
                if attempt >= API_RETRY:
                    raise RuntimeError(last_err)
                try:
                    retry_after = float(e.headers.get("Retry-After", ""))
                except Exception:
                    retry_after = 0.0
                if retry_after > 0:
                    time.sleep(min(retry_after, 30))
                continue
            # 其他 4xx 不重试（参数/鉴权问题），5xx 与网络错误走循环尾部重试
            if 400 <= e.code < 500:
                raise RuntimeError(last_err)
            if attempt >= API_RETRY:
                raise RuntimeError(last_err)
        except (URLError, OSError, TimeoutError) as e:
            last_err = str(getattr(e, "reason", e))
            if attempt >= API_RETRY:
                raise
    else:
        raise RuntimeError(f"API 连接失败（已重试 {API_RETRY} 次）：{last_err}")

    try:
        with resp:
            # 读取阶段用短超时：服务器不发数据的间隙也能及时响应「停止」
            try:
                resp.fp.raw._sock.settimeout(STREAM_READ_TIMEOUT)
            except Exception:
                pass
            buf = ""
            idle = 0.0
            it = iter(resp)  # HTTPResponse 迭代器逐行返回，SSE 每行一条 data，保证流式及时
            while True:
                try:
                    raw = next(it)
                except StopIteration:
                    break
                except socket.timeout:
                    # 服务器暂未发数据：趁机检查是否点了「停止」，保证思考阶段也能及时中断
                    if should_stop and should_stop():
                        try:
                            resp.close()
                        except Exception:
                            pass
                        raise InterruptedError("用户已停止生成")
                    idle += STREAM_READ_TIMEOUT
                    if idle >= STREAM_IDLE_LIMIT:
                        raise TimeoutError("流式读取超时（长时间无数据）")
                    continue
                if not raw:
                    break
                idle = 0.0
                buf += raw.decode("utf-8", errors="replace")
                while "\n\n" in buf:
                    event, buf = buf.split("\n\n", 1)
                    for line in event.splitlines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if payload == "[DONE]":
                            continue
                        if should_stop and should_stop():
                            try:
                                resp.close()
                            except Exception:
                                pass
                            raise InterruptedError("用户已停止生成")
                        try:
                            chunk = json.loads(payload)
                        except Exception:
                            continue
                        _handle_chunk(chunk, content, reasoning, tc_acc, on_text, on_reason)
                        fchoice = (chunk.get("choices") or [{}])[0]
                        fr = fchoice.get("finish_reason")
                        if fr:
                            finish_reason = fr
    except InterruptedError:
        raise
    except (URLError, OSError, TimeoutError, ConnectionError) as e:
        # 进入流式后中途断流：保留已生成内容，标记 error 交由上层提示/续写，避免整段丢失
        log("streaming interrupted: " + str(getattr(e, "reason", e)))
        finish_reason = "error"

    tool_calls = []
    for idx in sorted(tc_acc.keys()):
        tc = tc_acc[idx]
        args_raw = tc.get("arguments", "")
        try:
            parsed = json.loads(args_raw) if args_raw.strip() else {}
        except Exception:
            parsed = {"_raw": args_raw}
        tool_calls.append({
            "id": tc.get("id"),
            "type": tc.get("type", "function"),
            "function": {"name": tc.get("name"), "arguments": parsed},
        })
    if finish_reason == "error":
        # 断流时工具调用参数可能不完整（arguments 未闭合），丢弃只保留已生成文本，
        # 交由上层自动续传，避免残缺 tool_calls 导致下一轮 API 400。
        tool_calls = []
    return "".join(content), "".join(reasoning), tool_calls, finish_reason


def compact_history(history, force=False):
    """上下文压缩：history 超过硬上限（条数或字符）时，把最旧的部分压缩为一条
    摘要 system 消息，保留最近 CTX_KEEP_RECENT 条完整。纯本地、不额外调模型，
    避免历史无限膨胀导致 API 请求超时/中断。返回 (新history, 是否压缩)。
    force=True 时忽略阈值强制压缩（用于发送前体积兜底）。

    注意：切分点必须落在合法的 assistant↔tool 配对边界，否则保留窗口第一条是
    孤立 tool 消息（其 assistant(tool_calls) 已被裁掉）时，DeepSeek 会返回
    HTTP 400 "Messages with role 'tool' must be a response to a preceding message
    with 'tool_calls'"，导致长对话请求失败中断。"""
    if not history:
        return history, False
    n = len(history)
    size = sum(len(json.dumps(m, ensure_ascii=False)) for m in history)
    if not force and n <= CTX_HARD_LIMIT and size <= CTX_SOFT_LIMIT:
        return history, False

    # 保留最近 N 条，但窗口头部向前回退：跳过窗口开头的孤立 tool 消息，
    # 直到落在 user / assistant 消息上，保证 assistant(tool_calls)→tool 配对完整。
    start = max(0, n - CTX_KEEP_RECENT)
    while start < n and history[start].get("role") == "tool":
        start -= 1
    if start < 0:
        start = 0
    if start == 0:
        # 没有旧消息可压缩（条数本就在保留窗口内，如单条超大消息场景），
        # 返回原样，交由上层做单条截断，避免凭空加一条空摘要反而变大。
        return history, False
    old = history[:start]
    recent = history[start:]

    # 尾部防御：若窗口最后一条 assistant 带 tool_calls 却无对应 tool 响应
    #（正常流程不会发生，但落盘裁剪等异常路径可能产生），裁掉避免 dangling tool_calls。
    while recent and recent[-1].get("role") == "assistant" and recent[-1].get("tool_calls"):
        recent.pop()
    if not recent:  # 极端兜底：全被裁掉则保留最近一条
        recent = history[-1:]

    # 从被裁掉的部分提取早期 user 问题要点，作为一条摘要保留线索（不调模型，零失败）
    hints = []
    for m in old:
        if m.get("role") == "user" and m.get("content"):
            s = str(m["content"]).strip().replace("\n", " ")
            if s:
                hints.append(s[:80])
    summary = "；".join(hints[-12:]) if hints else "（早期对话已省略）"
    if len(summary) > 600:
        summary = summary[:600] + "…"

    new = [{"role": "system", "content": "[早期对话摘要] " + summary}] + recent
    return new, True


def ensure_request_size(messages, hard=CTX_HARD_BYTES):
    """发送前兜底：请求体序列化体积超过硬上限时强制压缩（保留首条 system prompt + 最近消息），
    并截断单条超长消息，避免 API 请求体超限/超时导致长任务中断。
    返回 (messages, 是否处理过)。"""
    size = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
    if size <= hard:
        return messages, False
    head = []
    rest = messages
    if messages and messages[0].get("role") == "system":
        head = [messages[0]]
        rest = messages[1:]
    new_rest, _ = compact_history(rest, force=True)
    out = head + new_rest
    truncated = False
    for m in out:
        c = m.get("content")
        if isinstance(c, str) and len(c) > MAX_MSG_CHARS:
            m["content"] = c[:MAX_MSG_CHARS] + "\n\n[内容过长，已自动截断]"
            truncated = True
    return out, True


def sanitize_messages(messages):
    """防御性清洗：删除无法配对的孤立 tool 消息，并去掉末尾悬空的
    tool_calls（assistant 带 tool_calls 但缺 tool 响应）。长会话落盘裁剪
    （pop(0)）也可能产生孤立 tool 消息，这里统一兜底，避免 DeepSeek 400。"""
    if not messages:
        return messages
    out = []
    pending = set()  # 尚未被 tool 响应消费的 tool_call_id
    for m in messages:
        role = m.get("role")
        if role == "tool":
            tid = m.get("tool_call_id")
            if not tid or tid not in pending:
                continue  # 孤立 tool 消息，丢弃
            pending.discard(tid)
            out.append(m)
        else:
            if role == "assistant":
                # 跳过 content 为空且没有 tool_calls 的 assistant 消息，
                # 否则 DeepSeek 会返回 400: "content or tool_calls must be set"
                if not m.get("content") and not m.get("tool_calls"):
                    continue
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        if tc and tc.get("id"):
                            pending.add(tc["id"])
            out.append(m)
    while out and out[-1].get("role") == "assistant" and out[-1].get("tool_calls"):
        out.pop()
    return out


def _handle_chunk(chunk, content, reasoning, tc_acc, on_text, on_reason):
    try:
        delta = chunk["choices"][0]["delta"]
    except (KeyError, IndexError):
        return
    r = delta.get("reasoning_content")
    if r:
        reasoning.append(r)
        if on_reason:
            on_reason(r)
    t = delta.get("content")
    if t:
        content.append(t)
        if on_text:
            on_text(t)
    for tc in delta.get("tool_calls", []) or []:
        idx = tc.get("index", 0)
        slot = tc_acc.setdefault(idx, {"id": None, "type": "function", "name": None, "arguments": ""})
        if tc.get("id"):
            slot["id"] = tc["id"]
        if tc.get("type"):
            slot["type"] = tc["type"]
        fn = tc.get("function", {})
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["arguments"] += fn["arguments"]


def build_system_prompt(cwd):
    return (
        "你是 MyCodex，一个 macOS 应用里的编码代理，底层由 DeepSeek 模型驱动。\n\n"
        f"工作目录：{cwd}\n"
        "你可以用以下工具来实际完成编码任务：\n"
        "  - read_file / write_file / edit_file：读写和修改文件\n"
        "  - list_dir：查看目录结构\n"
        "  - grep_files：在代码库中搜索内容\n"
        "  - run_command：执行 shell 命令（构建、测试、git 等）\n\n"
        "工作准则：\n"
        "  1. 先理解任务与上下文，再动手；修改前先用 read_file / list_dir 看清现状。\n"
        "  2. 优先用最小改动；edit_file 做局部修改，write_file 用于新建或整体重写。\n"
        "  3. 涉及破坏性命令（rm -rf、git reset --hard、sudo 等）时，先向用户说明意图。\n"
        "  4. 遇到错误就读报错、查上下文，自主修复，不要反复问同一问题。\n"
        "  5. 用简体中文回复（除非用户使用其他语言）；完成后用 2-4 句话总结做了什么。\n"
        "  6. 不要编造文件内容；未确认存在的路径先用工具核实。\n"
    )


def build_vision_system_prompt():
    return (
        "你是 MyCodex 的视觉助手，由通义千问 Qwen-VL 驱动，能够看懂图片和文档。\n"
        "请仔细观察用户提供的图片 / 文档内容，结合用户的问题，用简体中文清晰、准确地回答。\n"
        "如果图片是截图或代码，请完整转录或提取关键信息；如果看不清楚，如实说明，不要编造。\n"
    )


# --------------------------------------------------------------------------
# 千问多模态（Qwen-VL）：图片 / PDF 文档理解
# --------------------------------------------------------------------------
def _pdf_to_image_dataurls(data_bytes, max_pages=VISION_MAX_PDF_PAGES):
    """用 macOS Quartz 把 PDF 字节渲染成 PNG，返回 data URL 列表（最多 max_pages 页）。"""
    try:
        import base64
        import tempfile
        import Foundation
        import Quartz
    except Exception as e:
        raise RuntimeError(f"PDF 渲染依赖不可用：{e}")

    data = Foundation.NSData.dataWithBytes_length_(data_bytes, len(data_bytes))
    if not data or len(data) == 0:
        raise RuntimeError("PDF 数据为空")
    provider = Quartz.CGDataProviderCreateWithCFData(data)
    pdf = Quartz.CGPDFDocumentCreateWithProvider(provider)
    if not pdf:
        raise RuntimeError("无法解析 PDF 文件")
    total = Quartz.CGPDFDocumentGetNumberOfPages(pdf)
    if total == 0:
        raise RuntimeError("PDF 没有页面")
    pages = min(total, max_pages)
    out = []
    for i in range(1, pages + 1):
        page = Quartz.CGPDFDocumentGetPage(pdf, i)
        rect = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        scale = 2.0
        w = max(1, int(rect.size.width * scale))
        h = max(1, int(rect.size.height * scale))
        cs = Quartz.CGColorSpaceCreateDeviceRGB()
        ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, cs, Quartz.kCGImageAlphaPremultipliedLast)
        if not ctx:
            raise RuntimeError("创建位图上下文失败")
        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))
        Quartz.CGContextScaleCTM(ctx, scale, scale)
        Quartz.CGContextDrawPDFPage(ctx, page)
        img = Quartz.CGBitmapContextCreateImage(ctx)
        if not img:
            continue
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            url = Foundation.NSURL.fileURLWithPath_(tmp)
            dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
            if not dest:
                continue
            Quartz.CGImageDestinationAddImage(dest, img, None)
            if Quartz.CGImageDestinationFinalize(dest):
                b64 = base64.b64encode(Path(tmp).read_bytes()).decode("ascii")
                out.append("data:image/png;base64," + b64)
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass
    if not out:
        raise RuntimeError("PDF 渲染失败，未生成任何页面图片")
    return out


def _build_vision_content(text, images):
    """构造多模态 content 数组：文本 + 多张图片/PDF（OpenAI 兼容格式）。"""
    parts = []
    if text and text.strip():
        parts.append({"type": "text", "text": text.strip()})
    if images:
        for image in images:
            if not image:
                continue
            if image.startswith("data:application/pdf"):
                import base64
                _, _, b64 = image.partition(",")
                try:
                    pages = _pdf_to_image_dataurls(base64.b64decode(b64))
                except Exception as e:
                    raise RuntimeError(f"PDF 转图片失败：{e}")
                for p in pages:
                    parts.append({"type": "image_url", "image_url": {"url": p}})
            elif image.startswith("data:image/"):
                parts.append({"type": "image_url", "image_url": {"url": image}})
            else:
                raise RuntimeError("不支持的附件格式")
    if not parts:
        raise RuntimeError("没有可发送的内容")
    return parts


def stream_vision(messages, cfg, max_tokens=VISION_MAX_TOKENS, on_text=None, should_stop=None):
    """调用千问 VL（OpenAI 兼容接口，流式，无工具）。返回完整回复文本。"""
    v = _vision_cfg(cfg)
    if not v["api_key"]:
        raise RuntimeError("未配置千问视觉模型 Key（config.json 的 vision_api_key 或环境变量 DASHSCOPE_API_KEY）")
    body = {
        "model": v["model"],
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(
        v["base_url"].rstrip("/") + "/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {v['api_key']}",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    content = []
    reasoning = []
    tc_acc = {}
    with urlopen(req, timeout=API_CONNECT_TIMEOUT) as resp:
        try:
            resp.fp.raw._sock.settimeout(STREAM_READ_TIMEOUT)
        except Exception:
            pass
        buf = ""
        idle = 0.0
        it = iter(resp)
        while True:
            try:
                raw = next(it)
            except StopIteration:
                break
            except socket.timeout:
                if should_stop and should_stop():
                    try:
                        resp.close()
                    except Exception:
                        pass
                    raise InterruptedError("用户已停止生成")
                idle += STREAM_READ_TIMEOUT
                if idle >= STREAM_IDLE_LIMIT:
                    raise TimeoutError("流式读取超时（长时间无数据）")
                continue
            if not raw:
                break
            idle = 0.0
            buf += raw.decode("utf-8", errors="replace")
            while "\n\n" in buf:
                event, buf = buf.split("\n\n", 1)
                for line in event.splitlines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        continue
                    if should_stop and should_stop():
                        try:
                            resp.close()
                        except Exception:
                            pass
                        raise InterruptedError("用户已停止生成")
                    try:
                        chunk = json.loads(payload)
                    except Exception:
                        continue
                    _handle_chunk(chunk, content, reasoning, tc_acc, on_text, None)
    return "".join(content)


# --------------------------------------------------------------------------
# 会话管理
# --------------------------------------------------------------------------
def _short_cwd(path):
    s = str(path)
    home = str(Path.home())
    if s.startswith(home):
        s = "~" + s[len(home):]
    if len(s) > 50:
        s = "…" + s[-47:]
    return s


def _fmt_size(n):
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 / 1024:.1f}MB"


def _safe_name(name):
    """把任务名转换成可安全用作文件名的形式。"""
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:80] or "任务"


def _atomic_write_json(path, data):
    """崩溃安全的原子写：先写 .tmp（含 fsync 落盘），再 rename 替换目标文件。
    避免强杀/断电导致会话文件写一半损坏。"""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _cleanup_orphan_tmp():
    """启动时清理残留的 .tmp 文件（上次进程异常退出可能留下，不影响主文件）。"""
    try:
        if SESSIONS_DIR.exists():
            for p in SESSIONS_DIR.glob("*.tmp"):
                try:
                    p.unlink()
                except Exception:
                    pass
    except Exception:
        pass


# --------------------------------------------------------------------------
# 前端桥接
# --------------------------------------------------------------------------
def _current_window():
    try:
        if webview.windows:
            return webview.windows[0]
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# API（暴露给前端 JS；非下划线方法均可被调用）
# --------------------------------------------------------------------------
class Api:
    def __init__(self):
        self.cfg = load_config()
        self.cwd = Path.home()
        self.model = self.cfg.get("model", DEFAULT_MODEL)
        self.think_mode = "auto"  # off / auto / on
        self.history = []         # 喂给 API 的消息
        self.transcript = []      # 界面展示用（user/assistant/tool 等）
        self.artifacts = []       # 本次任务产物
        self.session_name = None
        self.pinned = False
        self.parent = None
        self._pending_images = []        # 待发送的图片/PDF data URL 列表
        self._pending_image_kind = None  # "vision" | "ocr"
        self._ctx = None                 # agent 线程上下文（生成中非空；写盘固定到发送时的任务）
        self._stop = False
        self._asst_open = False
        self._asst_text = ""
        self._pending_confirm = None
        self._last_ctx_save = 0.0      # 上次增量落盘时间（节流用，防频繁 IO）

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_orphan_tmp()
        self._restore_last()
        log("Api init; model=%s; has_key=%s" % (self.model, bool(self.cfg.get("api_key"))))

    # -------- 工具：推送到前端 --------
    def call_js(self, func, *args):
        w = _current_window()
        if not w:
            return
        try:
            arg_str = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
            w.evaluate_js(f"{func}({arg_str})")
        except Exception as e:
            log("evaluate_js error: " + str(e))

    # -------- 状态 --------
    def _state(self):
        return {
            "model": self.model,
            "think_mode": self.think_mode,
            "cwd": str(self.cwd),
            "cwd_short": _short_cwd(self.cwd),
            "session_name": self.session_name,
            "artifacts": self.artifacts,
            "transcript": self.transcript[-PLAYBACK_LIMIT:] if self.session_name else [],
            "transcript_total": len(self.transcript) if self.session_name else 0,
            "sessions": self.list_sessions(),
            "need_key": not bool(self.cfg.get("api_key")),
            "vision_ok": bool(_vision_cfg(self.cfg).get("api_key")),
        }

    def init(self):
        return self._state()

    def list_sessions(self):
        out = []
        if SESSIONS_DIR.exists():
            for p in SESSIONS_DIR.glob("*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                out.append({
                    "name": d.get("name", p.stem),
                    "msg_count": len(d.get("transcript", [])),
                    "updated": d.get("updated", p.stat().st_mtime),
                    "pinned": bool(d.get("pinned")),
                    "parent": d.get("parent") or None,
                })
        # 置顶任务在前，其余按更新时间倒序
        out.sort(key=lambda x: (not x["pinned"], -x["updated"]))
        return out

    # -------- 任务管理 --------
    def _unique_name(self, base):
        name = base
        i = 2
        while (SESSIONS_DIR / f"{name}.json").exists():
            name = f"{base} {i}"
            i += 1
        return name

    def new_task(self, name=None, parent=None):
        base = _safe_name(name) or "任务"
        self.session_name = self._unique_name(base)
        self.history = []
        self.transcript = []
        self.artifacts = []
        self.pinned = False
        self.parent = _safe_name(parent) if parent else None
        self._save()
        try:
            (CONFIG_DIR / "last_task.txt").write_text(self.session_name, encoding="utf-8")
        except Exception:
            pass
        return self._state()

    def _find_session_file(self, name):
        """按任务名定位会话文件：优先 {name}.json；找不到时扫描内部 name 兜底，
        兼容"文件名与内部 name 不一致"的悬空任务。返回 (路径, 真实name) 或 (None, None)。"""
        p = SESSIONS_DIR / f"{name}.json"
        if p.exists():
            return p, name
        # 兜底：扫描所有会话，按内部 name 匹配
        for cp in SESSIONS_DIR.glob("*.json"):
            try:
                cd = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if cd.get("name") == name:
                return cp, cd.get("name")
        return None, None

    def open_task(self, name):
        p, real_name = self._find_session_file(name)
        if p is None:
            return {"error": "not_found"}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": str(e)}
        # 以文件内部 name 为准（防止文件名与 name 不一致导致后续找不到）
        name = real_name or name
        self.session_name = name
        self.history = data.get("history", [])
        self.transcript = data.get("transcript", [])
        self.artifacts = data.get("artifacts", [])
        self.model = data.get("model", self.model)
        self.think_mode = data.get("think_mode", self.think_mode)
        self.pinned = bool(data.get("pinned"))
        self.parent = data.get("parent") or None
        cwd = data.get("cwd")
        if cwd and Path(cwd).exists():
            self.cwd = Path(cwd)
        try:
            (CONFIG_DIR / "last_task.txt").write_text(name, encoding="utf-8")
        except Exception:
            pass
        return {
            "transcript": self.transcript[-PLAYBACK_LIMIT:],
            "transcript_total": len(self.transcript),
            "artifacts": self.artifacts,
            "model": self.model,
            "think_mode": self.think_mode,
            "cwd": str(self.cwd),
            "cwd_short": _short_cwd(self.cwd),
            "name": name,
        }

    def load_earlier(self, offset):
        """分页加载更早的历史消息（每页 PLAYBACK_LIMIT 条）。
        offset 为当前已展示条数；返回 {entries, remaining}，供 UI 顶部「加载更早」使用。"""
        if not self.session_name:
            return {"entries": [], "remaining": 0}
        p = SESSIONS_DIR / f"{self.session_name}.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": str(e)}
        tr = data.get("transcript", [])
        # 已展示 offset 条（时间正序末尾）；更早内容位于其之前
        end = max(0, len(tr) - int(offset or 0))
        start = max(0, end - PLAYBACK_LIMIT)
        return {"entries": tr[start:end], "remaining": start}

    def _restore_last(self):
        """启动时自动恢复上次打开的任务（不推送前端，由 init 返回的 state 携带历史）。"""
        try:
            p = CONFIG_DIR / "last_task.txt"
            if p.exists():
                name = p.read_text(encoding="utf-8").strip()
                if name:
                    fp, _ = self._find_session_file(name)
                    if fp is not None:
                        self.open_task(name)
        except Exception:
            pass

    def rename_task(self, old_name, new_name):
        """重命名任务：改文件名 + 同步更新 JSON 内 name 与子任务 parent。"""
        old = _safe_name(old_name)
        new = _safe_name(new_name)
        if not new:
            return {"error": "任务名不能为空"}
        if new == old:
            return {"ok": True, "name": old}
        old_p = SESSIONS_DIR / f"{old}.json"
        new_p = SESSIONS_DIR / f"{new}.json"
        if not old_p.exists():
            return {"error": "任务不存在"}
        if new_p.exists():
            return {"error": f"已存在同名任务：{new}"}
        try:
            data = json.loads(old_p.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": str(e)}
        data["name"] = new
        old_p.rename(new_p)
        # 同步更新子任务的 parent 字段
        for cp in SESSIONS_DIR.glob("*.json"):
            try:
                cd = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if cd.get("parent") == old:
                cd["parent"] = new
                _atomic_write_json(cp, cd)
        # 写回改名后的父任务
        _atomic_write_json(new_p, data)
        # 若当前打开的就是被改名的任务，同步内存状态
        if self.session_name == old:
            self.session_name = new
        return {"ok": True, "name": new}

    def toggle_pin(self, name):
        """切换任务置顶状态。"""
        name = _safe_name(name)
        p = SESSIONS_DIR / f"{name}.json"
        if not p.exists():
            return {"error": "任务不存在"}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": str(e)}
        data["pinned"] = not bool(data.get("pinned"))
        _atomic_write_json(p, data)
        if self.session_name == name:
            self.pinned = data["pinned"]
        return {"ok": True, "pinned": data["pinned"]}

    def new_subtask(self, parent, name=None):
        """在父任务下新建子任务（空会话，不切换当前上下文）。"""
        parent = _safe_name(parent)
        if not (SESSIONS_DIR / f"{parent}.json").exists():
            return {"error": "父任务不存在"}
        child = self._unique_name(_safe_name(name) or "子任务")
        data = {
            "name": child,
            "model": self.model,
            "think_mode": self.think_mode,
            "cwd": str(self.cwd),
            "updated": time.time(),
            "pinned": False,
            "parent": parent,
            "history": [],
            "transcript": [],
            "artifacts": [],
        }
        p = SESSIONS_DIR / f"{child}.json"
        _atomic_write_json(p, data)
        return {"ok": True, "name": child, "parent": parent}

    def delete_task(self, name):
        """删除任务。若存在子任务则级联一并删除，返回被删除的名单。"""
        name = _safe_name(name)
        p = SESSIONS_DIR / f"{name}.json"
        if not p.exists():
            return {"error": "任务不存在"}

        # 递归收集该任务及其所有子孙任务
        to_delete = []

        def _collect(n):
            to_delete.append(n)
            for cp in SESSIONS_DIR.glob("*.json"):
                try:
                    cd = json.loads(cp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if cd.get("parent") == n:
                    _collect(cd.get("name", cp.stem))

        _collect(name)

        deleted = []
        for n in to_delete:
            fp = SESSIONS_DIR / f"{n}.json"
            try:
                fp.unlink()
                deleted.append(n)
            except Exception:
                pass

        # 若删除的是当前打开的任务，重置内存状态
        if self.session_name in to_delete:
            self.session_name = None
            self.history = []
            self.transcript = []
            self.artifacts = []
            self.pinned = False
            self.parent = None
            try:
                (CONFIG_DIR / "last_task.txt").unlink(missing_ok=True)
            except Exception:
                pass
        return {"ok": True, "deleted": deleted}

    def _save(self):
        if not self.session_name:
            return
        data = {
            "name": self.session_name,
            "model": self.model,
            "think_mode": self.think_mode,
            "cwd": str(self.cwd),
            "updated": time.time(),
            "pinned": self.pinned,
            "parent": self.parent,
            "history": self.history,
            "transcript": self.transcript,
            "artifacts": self.artifacts,
        }
        p = SESSIONS_DIR / f"{self.session_name}.json"
        try:
            _atomic_write_json(p, data)
        except Exception as e:
            log("保存会话失败：" + str(e))

    # -------- 设置 --------
    def set_model(self, model):
        self.model = model
        self.cfg["model"] = model
        self._save()
        return "ok"

    def set_think_mode(self, mode):
        if mode in ("off", "auto", "on"):
            self.think_mode = mode
            self._save()
        return "ok"

    def choose_dir(self):
        w = _current_window()
        if not w:
            return str(self.cwd)
        try:
            picked = w.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as e:
            log("choose_dir error: " + str(e))
            return str(self.cwd)
        if picked and len(picked) > 0:
            self.cwd = Path(picked[0]).resolve()
            self._save()
        return str(self.cwd)

    def stop(self):
        self._stop = True
        return "ok"

    def _is_self_html(self, html_text):
        """判断 HTML 是否是 MyCodex 自身主界面，避免 iframe 嵌套自己。"""
        markers = [
            "window.pywebview",
            'id="taskList"',
            'id="messages"',
            'class="topbar"',
            "pywebview.api",
        ]
        low = html_text.lower()
        return sum(1 for m in markers if m.lower() in low) >= 3

    def _inline_html_assets(self, html_path, html_text, max_asset_size=1024 * 1024):
        """
        把 HTML 中引用的本地相对资源（css/js/图片）内联，使 srcdoc 能正常渲染。
        只处理与 HTML 同目录的本地相对文件；http/https/data 链接保持原样。
        """
        import base64 as _b64
        base = Path(html_path).resolve().parent

        def _load(rel):
            rel = rel.strip().strip("\"'")
            if not rel or rel.startswith(("http://", "https://", "data:", "//", "#")):
                return None
            if rel.startswith("/"):
                return None
            try:
                fp = (base / rel).resolve()
                # 限制只能读取 HTML 所在目录下的文件，防目录遍历
                if not (fp == base or base in fp.parents):
                    return None
                if not fp.is_file() or fp.stat().st_size > max_asset_size:
                    return None
                return fp
            except Exception:
                return None

        def _css_repl(m):
            fp = _load(m.group(1))
            if not fp:
                return m.group(0)
            try:
                return f'<style>{fp.read_text(encoding="utf-8", errors="replace")}</style>'
            except Exception:
                return m.group(0)

        def _js_repl(m):
            fp = _load(m.group(1))
            if not fp:
                return m.group(0)
            try:
                return f'<script>{fp.read_text(encoding="utf-8", errors="replace")}</script>'
            except Exception:
                return m.group(0)

        def _img_repl(m):
            fp = _load(m.group(1))
            if not fp:
                return m.group(0)
            try:
                ext = fp.suffix.lower()
                mime = {
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                    ".ico": "image/x-icon", ".svg": "image/svg+xml",
                }.get(ext, "application/octet-stream")
                data = _b64.b64encode(fp.read_bytes()).decode("ascii")
                tag = m.group(0)
                tail = tag[4:]  # 去掉 '<img'
                return f'<img src="data:{mime};base64,{data}"{tail}'
            except Exception:
                return m.group(0)

        html_text = re.sub(
            r'<link[^>]*?rel=["\']stylesheet["\'][^>]*?href=["\']([^"\']+)["\'][^>]*?>',
            _css_repl, html_text, flags=re.IGNORECASE
        )
        html_text = re.sub(
            r'<script[^>]*?src=["\']([^"\']+)["\'][^>]*?></script>',
            _js_repl, html_text, flags=re.IGNORECASE
        )
        html_text = re.sub(
            r'<img[^>]*?src=["\']([^"\']+)["\'][^>]*?>',
            _img_repl, html_text, flags=re.IGNORECASE
        )
        return html_text

    def preview(self, path):
        """按文件类型返回可预览内容：
        image → base64 图片；html → 网页源码；md → 文本；pdf/二进制 → 提示外部打开；
        is_url → 文本首行是链接时识别为网页链接；其余文本 → 原样返回。"""
        try:
            import base64 as _b64
            p = Path(path)
            if not p.exists() or p.is_dir():
                return {"error": "文件不存在或不是普通文件"}
            name = p.name
            size = p.stat().st_size
            ext = p.suffix.lower()
            base = {"name": name, "size": size, "size_str": _fmt_size(size), "path": str(p)}
            # 图片：直接返回 data URL 供前端渲染
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg", ".avif"):
                if size > 10 * 1024 * 1024:
                    return {**base, "kind": "binary", "hint": "图片过大（>10MB），请用浏览器打开查看"}
                mime = {
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                    ".ico": "image/x-icon", ".svg": "image/svg+xml", ".avif": "image/avif",
                }.get(ext, "image/png")
                data = _b64.b64encode(p.read_bytes()).decode("ascii")
                return {**base, "kind": "image", "data_url": f"data:{mime};base64,{data}"}
            # PDF：浏览器/默认应用打开
            if ext == ".pdf":
                return {**base, "kind": "pdf"}
            # HTML：内嵌网页预览
            if ext in (".html", ".htm"):
                text = p.read_text(encoding="utf-8", errors="replace")
                text = self._inline_html_assets(str(p), text)
                is_self = self._is_self_html(text)
                return {**base, "kind": "html", "content": text, "force_source": bool(is_self)}
            # Markdown：渲染排版
            if ext in (".md", ".markdown"):
                text = p.read_text(encoding="utf-8", errors="replace")
                if len(text) > PREVIEW_LIMIT:
                    text = text[:PREVIEW_LIMIT] + "\n…（预览已截断）"
                return {**base, "kind": "md", "content": text}
            # 二进制
            raw = p.read_bytes()
            if b"\x00" in raw[:8192]:
                return {**base, "kind": "binary", "hint": "二进制文件，无法直接预览"}
            text = raw.decode("utf-8", errors="replace")
            is_url = False
            url = ""
            first = text.strip().splitlines()[0].strip() if text.strip() else ""
            if first.startswith(("http://", "https://")):
                is_url = True
                url = first
            if len(text) > PREVIEW_LIMIT:
                text = text[:PREVIEW_LIMIT] + "\n…（预览已截断）"
            return {**base, "kind": "text", "content": text, "is_url": is_url, "url": url}
        except Exception as e:
            return {"error": str(e)}

    def open_external(self, path):
        """用系统默认程序打开本地文件（HTML/PDF/图片会走默认浏览器/预览）。"""
        try:
            p = Path(path)
            if not p.exists():
                return {"error": "文件不存在"}
            import subprocess
            subprocess.Popen(["open", str(p)])
            return {"ok": True, "name": p.name}
        except Exception as e:
            return {"error": str(e)}

    def copy_text(self, text):
        """把文本写入 macOS 系统剪贴板（前端复制的兜底通道）。"""
        try:
            import AppKit
            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            ok = pb.setString_forType_(text or "", AppKit.NSPasteboardTypeString)
            return "ok" if ok else "error"
        except Exception as e:
            log("copy_text error: " + str(e))
            return "error"

    def open_url(self, url):
        """用系统默认浏览器打开 URL（产物中的链接点击）。"""
        import subprocess
        if not url or not url.startswith(("http://", "https://")):
            return {"error": "不是有效的 HTTP/HTTPS 链接"}
        try:
            subprocess.Popen(["open", url])
            return {"ok": True}
        except Exception as e:
            log("open_url error: " + str(e))
            return {"error": str(e)}

    def ocr_image(self, source):
        """识别图片中的文字（macOS Vision，支持中英文）。

        source 可以是 data URL（data:image/png;base64,...）或本地文件路径。
        返回 {"lines": [...], "text": "..."} 或 {"error": "..."}
        """
        try:
            import base64
            import Foundation
            import Quartz
            import Vision
        except Exception as e:
            return {"error": f"OCR 依赖不可用：{e}"}

        def _cgimage(src):
            try:
                if src.startswith("data:"):
                    _, _, b64 = src.partition(",")
                    raw = base64.b64decode(b64)
                    data = Foundation.NSData.dataWithBytes_length_(raw, len(raw))
                    return Quartz.CGImageSourceCreateWithData(data, None)
                url = Foundation.NSURL.fileURLWithPath_(src)
                return Quartz.CGImageSourceCreateWithURL(url, None)
            except Exception:
                return None

        try:
            cg_src = _cgimage(source)
            if not cg_src:
                return {"error": "无法解码图片"}
            cg = Quartz.CGImageSourceCreateImageAtIndex(cg_src, 0, None)
            if not cg:
                return {"error": "无法解码图片"}

            def _noop(r, e):
                pass

            req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_noop)
            req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            req.setRecognitionLanguages_(["zh-Hans", "zh-Hant", "en-US"])
            req.setUsesLanguageCorrection_(True)
            handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
            ok, err = handler.performRequests_error_([req], None)
            if not ok:
                return {"error": f"OCR 执行失败：{err}"}
            lines = []
            for obs in (req.results() or []):
                c = obs.topCandidates_(1)
                if c and len(c) > 0:
                    s = c[0].string()
                    if s:
                        lines.append(s)
            return {"lines": lines, "text": "\n".join(lines), "count": len(lines)}
        except Exception as e:
            log("ocr_image error: " + str(e))
            return {"error": str(e)}

    def confirm_response(self, ok):
        pc = self._pending_confirm
        if pc:
            pc["result"] = bool(ok)
            pc["event"].set()
        return "ok"

    # -------- 流式回调 --------
    def _ensure_assistant(self):
        if not self._asst_open:
            self.call_js("beginAssistant")
            self._asst_open = True
            self._asst_text = ""

    def _on_text(self, t):
        if self._stop:
            raise InterruptedError("用户已停止生成")
        self._ensure_assistant()
        self._asst_text += t
        self.call_js("appendAssistant", t)

    def _on_reason(self, r):
        # 思考阶段也响应停止：DeepSeek 深度思考可能持续很久，不能让停止按钮失效
        if self._stop:
            raise InterruptedError("用户已停止生成")
        self._ensure_assistant()
        self.call_js("appendReason", r)

    # -------- 工具执行 --------
    def add_artifact(self, path):
        ctx = self._ctx or {"artifacts": self.artifacts}
        self._add_artifact_ctx(ctx, path)

    def _add_artifact_ctx(self, ctx, path):
        p = Path(path)
        try:
            st = p.stat()
            size = st.st_size
            mtime = st.st_mtime
        except Exception:
            return
        entry = {"path": str(p), "name": p.name, "size": size, "mtime": mtime, "size_str": _fmt_size(size)}
        # 小文本文件：如果内容只有单个 URL，标记为可点击链接
        entry["is_url"] = False
        if size < 4096 and p.suffix.lower() in ("", ".txt", ".md", ".url", ".link"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore").strip()
                if text and text.splitlines()[0].strip().startswith(("http://", "https://")):
                    entry["is_url"] = True
                    entry["url"] = text.splitlines()[0].strip()
            except Exception:
                pass
        arts = ctx["artifacts"]
        for i, a in enumerate(arts):
            if a["path"] == str(p):
                arts[i] = entry
                self.call_js("updateArtifact", entry)
                return
        arts.append(entry)
        self.call_js("addArtifact", entry)

    def _save_ctx(self, ctx):
        """按线程局部上下文写盘，目标固定为 ctx['task']（防任务串写）。"""
        target = ctx.get("task")
        if not target:
            return
        data = {
            "name": target,
            "model": self.model,
            "think_mode": self.think_mode,
            "cwd": str(self.cwd),
            "updated": time.time(),
            "pinned": ctx.get("pinned", False),
            "parent": ctx.get("parent"),
            "history": ctx["history"],
            "transcript": ctx["transcript"],
            "artifacts": ctx["artifacts"],
        }
        # 落盘体积保护：超硬上限时丢弃最旧段，避免会话文件膨胀到数 MB 导致加载卡死
        est = lambda lst: sum(len(json.dumps(m, ensure_ascii=False)) for m in (lst or []))
        hist = ctx.get("history") or []
        tr = ctx.get("transcript") or []
        while est(hist) > TRANSCRIPT_HARD_LIMIT and len(hist) > 2:
            hist.pop(0)
        while est(tr) > TRANSCRIPT_HARD_LIMIT and len(tr) > 2:
            tr.pop(0)
        ctx["history"] = hist
        ctx["transcript"] = tr

        p = SESSIONS_DIR / f"{target}.json"
        try:
            _atomic_write_json(p, data)
        except Exception as e:
            log("保存会话失败：" + str(e))

    def _save_ctx_throttled(self, ctx, interval=2.0):
        """节流落盘：距上次写盘超过 interval 秒才写，避免频繁 IO 拖慢生成。
        崩溃/强杀时最多丢失最近几秒的增量，任务与用户消息不会整轮消失。"""
        now = time.time()
        if now - getattr(self, "_last_ctx_save", 0.0) < interval:
            return
        self._last_ctx_save = now
        self._save_ctx(ctx)

    def _approve(self, command):
        self._pending_confirm = {"event": threading.Event(), "result": False}
        self.call_js("showConfirm", command)
        # 180s 内无人处理弹窗则按拒绝处理，避免生成线程被弹窗永久挂起（此前 300s 太久）
        self._pending_confirm["event"].wait(timeout=180)
        res = self._pending_confirm["result"]
        self._pending_confirm = None
        return res

    def _run_tool(self, tc):
        # 工具执行前响应停止：防止模型连续调用多个耗时工具时无法中断
        if self._stop:
            raise InterruptedError("用户已停止生成")
        name = tc["function"]["name"]
        args = tc["function"]["arguments"]
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        summary = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
        if len(summary) > 240:
            summary = summary[:240] + " …"
        self.call_js("appendTool", name, summary, "")

        if name in TOOL_IMPL:
            result = _run_tool_with_timeout(name, args, self.cwd)
            if name in ("write_file", "edit_file"):
                try:
                    self.add_artifact(str(_resolve(args["path"], self.cwd)))
                except Exception:
                    pass
        elif name == "run_command":
            result = tool_run_command(args, self.cwd, approve_fn=self._approve)
        else:
            result = f"未知工具：{name}"

        display = result
        if len(display) > CMD_OUTPUT_LIMIT:
            display = display[:CMD_OUTPUT_LIMIT] + f"\n…（输出已截断，共 {len(result)} 字符）"
        self.call_js("appendToolResult", name, summary, display)
        ctx = self._ctx or {"history": self.history, "transcript": self.transcript, "artifacts": self.artifacts}
        ctx["transcript"].append({"role": "tool", "name": name, "text": f"{name}({summary})\n{display}"})
        # 返回给模型的结果也统一截断：read_file 等文件工具可能吐出超长内容，
        # 直接进 messages 会撑爆单条消息与上下文，导致下一轮请求超时/超限中断
        return _truncate(str(result), CMD_OUTPUT_LIMIT)

    # -------- 主循环 --------
    def send(self, text, images=None):
        text = (text or "").strip()
        images = images or []
        if not text and not images:
            return "empty"
        if not self.cfg.get("api_key"):
            self.call_js("appendSystem", "未配置 API Key，请在 ~/.config/mycode/config.json 设置。")
            return "no_key"
        if images:
            self._pending_images = list(images)
            # 配了视觉模型 Key 用视觉模型（能看画面/图表/公式）；否则回退本地 OCR（免费、纯文字截图够用）
            self._pending_image_kind = "vision" if _vision_cfg(self.cfg).get("api_key") else "ocr"
        if not self.session_name:
            self.new_task()
        # 创建【线程局部上下文】快照：agent 线程只写这份数据，
        # 结束后固定写回"发送时所在的任务"，即使期间切换任务也不会串文件。
        self._ctx = {
            "task": self.session_name,
            "history": list(self.history),
            "transcript": list(self.transcript),
            "artifacts": list(self.artifacts),
            "pinned": self.pinned,
            "parent": self.parent,
        }
        # 独立线程跑 agent loop，避免阻塞 pywebview 的 API 线程池
        # （否则危险命令确认弹窗会因线程被占而卡死）
        threading.Thread(target=self._run_agent, args=(text,), daemon=True).start()
        return "ok"

    def _run_agent(self, text):
        self._stop = False
        self._asst_open = False
        ctx = self._ctx
        self.call_js("setBusy", True)
        self.call_js("appendUser", text, len(self._pending_images))
        if ctx is not None:
            ctx["transcript"].append({"role": "user", "text": text, "image": len(self._pending_images)})
            # 用户消息立即落盘：即使生成中崩溃/强杀，本条消息与任务文件也不会整轮丢失
            self._save_ctx(ctx)
            self._last_ctx_save = time.time()
        try:
            if self._pending_images and self._pending_image_kind == "vision":
                self._vision_loop(text)
            elif self._pending_images:
                self._ocr_then_agent(text)
            else:
                self._agent_loop(text)
        except InterruptedError:
            self.call_js("appendSystem", "已停止")
        except Exception as e:
            # daemon 线程的意外异常不会触发 sys.excepthook，必须在这里显式留档，
            # 否则视觉/OCR/主循环的任何漏网异常都只会静默消失
            log_error(e, "run_agent", ctx)
            self.call_js("appendSystem", f"错误：{e}")
        finally:
            self._pending_images = []
            self._pending_image_kind = None
            self.call_js("setBusy", False)
            if ctx is not None:
                # 固定写回发送时所在的任务文件（不依赖当前 session_name）
                self._save_ctx(ctx)
                # 若用户仍未切走，同步内存状态；若已切走则不动，打开时再从文件读
                if self.session_name == ctx["task"]:
                    self.history = ctx["history"]
                    self.transcript = ctx["transcript"]
                    self.artifacts = ctx["artifacts"]
                self._ctx = None
            self.call_js("afterSend")

    def _ocr_then_agent(self, user_input):
        """无视觉模型 Key 时的兜底：本地 macOS Vision OCR 提取附件文字，
        拼进用户消息后走正常 DeepSeek agent loop（免费、纯本地、截图/文档文字场景够用）。"""
        images = self._pending_images
        self.call_js("appendSystem", "未配置视觉模型，改用本地 OCR 提取附件文字…")
        ocr_texts = []
        try:
            for idx, image in enumerate(images, 1):
                if not image:
                    continue
                tag = f"[图片{idx}] " if len(images) > 1 else ""
                if image.startswith("data:application/pdf"):
                    import base64
                    _, _, b64 = image.partition(",")
                    pages = _pdf_to_image_dataurls(base64.b64decode(b64))
                    for i, p in enumerate(pages, 1):
                        r = self.ocr_image(p)
                        if r.get("text"):
                            ocr_texts.append(f"{tag}[第{i}页]\n{r['text']}")
                elif image.startswith("data:image/"):
                    r = self.ocr_image(image)
                    if r.get("error"):
                        self.call_js("appendSystem", f"OCR 失败：{r['error']}")
                    elif r.get("text"):
                        ocr_texts.append(tag + r["text"])
        except Exception as e:
            self.call_js("appendSystem", f"OCR 处理失败：{e}")

        ocr_joined = "\n\n".join(ocr_texts).strip()
        if not ocr_joined:
            self.call_js("appendSystem",
                         "本地 OCR 未识别到文字（可能是纯图像/图表）。如需看懂画面本身，"
                         "请在 ~/.config/mycode/config.json 配置 vision_api_key 启用视觉模型。")
        # 组装注入了 OCR 结果的用户消息，交给正常 agent loop
        if ocr_joined:
            merged = (
                f"{user_input}\n\n"
                f"[以下是我随消息附带的图片/文档，通过本地 OCR 提取出的文字内容，请据此理解并回答]\n"
                f"```\n{ocr_joined}\n```"
            )
        else:
            merged = user_input
        self._agent_loop(merged)

    def _call_chat(self, messages, thinking):
        """包装 stream_chat：主模型（通义）遇账户/鉴权致命错误时，自动切 DeepSeek 续跑同一轮。"""
        eff = _FALLBACK_MODEL if self._using_fallback else self.cfg.get("model")
        try:
            return stream_chat(
                messages, self.cfg, TOOLS, thinking, MAX_TOKENS,
                on_text=self._on_text,
                should_stop=lambda: self._stop,
                model=eff,
            )
        except RuntimeError as e:
            if not self._using_fallback and _fallback_available(self.cfg) and _is_fatal_provider_err(e):
                self._using_fallback = True
                self.call_js("appendSystem",
                             "主模型（通义）账户/鉴权不可用，已自动切换到深度·Flash 继续。")
                return stream_chat(
                    messages, self.cfg, TOOLS, thinking, MAX_TOKENS,
                    on_text=self._on_text,
                    should_stop=lambda: self._stop,
                    model=_FALLBACK_MODEL,
                )
            raise

    def _vision_loop(self, user_input):
        """多模态单轮：把图片/PDF（支持多张）与历史文字上下文一起发给视觉模型。"""
        images = self._pending_images
        v = _vision_cfg(self.cfg)
        ctx = self._ctx or {
            "task": self.session_name,
            "history": self.history,
            "transcript": self.transcript,
            "artifacts": self.artifacts,
        }
        try:
            content = _build_vision_content(user_input, images)
        except Exception as e:
            self.call_js("appendSystem", f"附件处理失败：{e}")
            return
        history, compacted = compact_history(ctx["history"])
        if compacted:
            self.call_js("appendSystem", "对话历史较长，已自动压缩早期内容以保持流畅")
        messages = [{"role": "system", "content": build_vision_system_prompt()}] + history
        messages.append({"role": "user", "content": content})
        # history 可能混入压缩/中断产生的空 assistant 消息，统一清洗防 400
        messages = sanitize_messages(messages)
        self._ensure_assistant()
        try:
            reply = stream_vision(messages, self.cfg, VISION_MAX_TOKENS, on_text=self._on_text,
                                  should_stop=lambda: self._stop)
        except InterruptedError:
            # 用户点了停止：向上传递，由 _run_agent 统一显示「已停止」，不触发 OCR 兜底
            raise
        except Exception as e:
            self.call_js("appendSystem", f"视觉模型调用失败：{e}")
            # 视觉失败自动降级到本地 OCR 兜底，尽量不丢失用户意图
            self.call_js("appendSystem", "尝试用本地 OCR 文字兜底…")
            self._ocr_then_agent(user_input)
            return
        # 存档：图片/文档只在当前轮参与；history 存纯文字，保证后续 DeepSeek 上下文兼容
        ctx["history"].append({"role": "user", "content": user_input})
        ctx["history"].append({"role": "assistant", "content": reply})
        ctx["transcript"].append({"role": "assistant", "text": reply})
        self.call_js("appendSystem", f"完成（已用视觉模型 {v['provider']}/{v['model']} 理解图片/文档）")

    def _agent_loop(self, user_input):
        self._stop = False
        self._using_fallback = False
        ctx = self._ctx or {
            "task": self.session_name,
            "history": self.history,
            "transcript": self.transcript,
            "artifacts": self.artifacts,
        }
        # 上下文压缩：防止历史无限膨胀导致请求超时/中断
        history, compacted = compact_history(ctx["history"])
        if compacted:
            self.call_js("appendSystem", "对话历史较长，已自动压缩早期内容以保持流畅")
        messages = [
            {"role": "system", "content": build_system_prompt(self.cwd)},
        ] + history + [
            {"role": "user", "content": user_input},
        ]
        # 防御性清洗：丢弃压缩/落盘裁剪产生的孤立 tool 消息与悬空 tool_calls，
        # 避免 DeepSeek 返回 400 invalid_request_error 导致长对话中断
        messages = sanitize_messages(messages)
        thinking = resolve_think(self.think_mode, user_input)

        last_sig = None
        repeat = 0
        attempt_continue = 0
        try:
            while True:
                # 发送前体积兜底：历史膨胀/单条超大消息时强制压缩，防请求体超限中断
                messages, shrunk = ensure_request_size(messages)
                if shrunk:
                    self.call_js("appendSystem", "上下文较大，已自动压缩以保持请求稳定")
                # 循环内防御：续写/工具轮次追加的消息也统一清洗，
                # 杜绝空 assistant 或孤立 tool 消息进入请求体
                messages = sanitize_messages(messages)
                content, reasoning, tool_calls, finish = self._call_chat(messages, thinking)
                assistant_msg = {"role": "assistant", "content": content or ""}
                if tool_calls:
                    sig = json.dumps(
                        [(tc["function"]["name"], tc["function"]["arguments"]) for tc in tool_calls],
                        ensure_ascii=False, sort_keys=True,
                    )
                    if sig == last_sig:
                        repeat += 1
                    else:
                        repeat = 0
                    last_sig = sig
                    if repeat >= MAX_REPEAT:
                        self.call_js("appendSystem", "检测到工具调用陷入重复，已自动停止；可继续发送「继续」重试")
                        break
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": tc["type"],
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": json.dumps(tc["function"]["arguments"], ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ]
                    messages.append(assistant_msg)
                    ctx["history"].append(assistant_msg)
                    if content:
                        ctx["transcript"].append({"role": "assistant", "text": content})
                    for tc in tool_calls:
                        result = self._run_tool(tc)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })
                        ctx["history"].append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })
                        # 每个工具执行完增量落盘：长任务中崩溃最多丢最后几秒
                        self._save_ctx_throttled(ctx)
                else:
                    # 长任务输出被 max_tokens 截断（length）或流式中断（error）：自动续写，直至完整或达上限
                    if finish in ("length", "error") and attempt_continue < MAX_CONTINUE:
                        attempt_continue += 1
                        # 关键：中断且未收到任何文本时，绝不能把空的 assistant 消息
                        # 追加进上下文，否则下一轮请求会触发 DeepSeek 400
                        # "content or tool_calls must be set"。
                        if content:
                            messages.append(assistant_msg)
                            ctx["history"].append(assistant_msg)
                            ctx["transcript"].append({"role": "assistant", "text": content})
                        if finish == "length":
                            self.call_js("appendSystem", f"输出较长，自动续写第 {attempt_continue} 段…")
                            messages.append({"role": "user", "content": "（上文被输出上限截断，请继续，不要重复已写内容，从断点处直接接着写，保持格式连贯）"})
                        else:
                            self.call_js("appendSystem", f"网络中断，自动续传第 {attempt_continue} 段…")
                            messages.append({"role": "user", "content": "（上文因网络中断未写完，请继续，不要重复已写内容，从断点处直接接着写，保持格式连贯）"})
                        # 续写前落盘已生成内容：中断/崩溃不丢已产出的文本
                        self._save_ctx_throttled(ctx)
                        continue
                    if finish == "error" and not content:
                        raise RuntimeError("网络中断，未收到完整回复，请重试")
                    messages.append(assistant_msg)
                    ctx["history"].append(assistant_msg)
                    if content:
                        ctx["transcript"].append({"role": "assistant", "text": content})
                    if finish == "error":
                        self.call_js("appendSystem", "网络多次中断，已保留已生成内容")
                    elif finish == "length":
                        self.call_js("appendSystem", "输出较长，已保留全部已生成内容，可发送「继续」补全剩余部分")
                    else:
                        self.call_js("appendSystem", "完成")
                    break
        except InterruptedError:
            self.call_js("appendSystem", "已停止")
        except Exception as e:
            log_error(e, "agent_loop", ctx)
            self.call_js("appendSystem", f"错误：{_friendly_err(e)}")
        finally:
            self._asst_open = False

    def _think_label(self):
        return {"off": "关", "auto": "自动", "on": "开"}.get(self.think_mode, "自动")


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def _acquire_single_instance():
    """单实例锁：同一时刻只允许一个 MyCodex 进程运行。

    用 fcntl 对 instance.lock 加非阻塞排他锁——锁随进程退出自动释放，
    即使强杀/崩溃也无需手动清理。副实例会尝试把已运行窗口激活到前台后退出，
    从根上消除「双进程抢 WebView/会话文件」导致的假死、白屏。"""
    try:
        import fcntl
    except ImportError:
        return None, True  # 非 Unix 平台放行（本项目面向 macOS）
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = CONFIG_DIR / "instance.lock"
        f = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # 已有实例持有锁：尝试把它带到前台，然后本进程退出
        try:
            import subprocess
            subprocess.run(
                ["osascript", "-e", 'tell application "%s" to activate' % APP_NAME],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        return None, False
    f.write("%d\n" % os.getpid())
    f.flush()
    return f, True


def _install_excepthook():
    """主线程未捕获异常也留档 error.log，避免 GUI 主循环崩溃时无迹可循。"""

    def _hook(etype, evalue, etb):
        try:
            import traceback
            lines = "".join(traceback.format_exception(etype, evalue, etb))
            ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ERROR_LOG, "a", encoding="utf-8") as _f:
                _f.write("%s [excepthook]\n%s\n---\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), lines))
        except Exception:
            pass

    sys.excepthook = _hook


def main():
    try:
        import traceback, time, glob, os
        _install_excepthook()
        lock_f, is_primary = _acquire_single_instance()
        if not is_primary:
            log("main: another instance running, activated it and exiting")
            return
        log("main: single-instance lock acquired")
        api = Api()
        here = Path(__file__).resolve().parent
        index = here / "index.html"
        log("main: index path = " + str(index))
        # 将 CSS/JS 直接内联到 HTML，避免 WebKit 主文档缓存 & 跨源错误脱敏
        html = index.read_text(encoding="utf-8")
        css = (here / "style.css").read_text(encoding="utf-8")
        js = (here / "app.js").read_text(encoding="utf-8")
        html = re.sub(
            r'<link rel="stylesheet" href="style\.css[^"]*"\s*/?>',
            lambda m: f"<style>\n{css}\n</style>",
            html,
        )
        js_safe = js.replace("</script>", "<\\/script>")
        html = re.sub(
            r'<script src="app\.js[^"]*"></script>',
            lambda m: f"<script>\n{js_safe}\n</script>",
            html,
        )
        log("main: inlined css/js, html length = " + str(len(html)))
        # 把 AppIcon.icns 提取成 base64 PNG，注入 HTML 顶栏 logo
        icon_b64 = ""
        icns_path = here.parent / "AppIcon.icns"  # AppIcon.icns 在 Resources/ 下，不是 app/ 下
        if icns_path.exists():
            try:
                from PIL import Image
                import base64, io
                img = Image.open(icns_path).convert("RGBA")
                # 取最大尺寸再缩到 96px，足够 Retina 显示
                src = max(img.size) if hasattr(img, "size") and img.size else 1024
                if isinstance(src, tuple):
                    src = src[0]
                img2 = img.resize((96, 96), Image.LANCZOS)
                buf = io.BytesIO()
                img2.save(buf, format="PNG")
                icon_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                log("main: icon embedded (%d bytes)" % len(icon_b64))
            except Exception as e:
                log("main: icon embed failed: " + str(e))
        html = html.replace("{{APP_ICON}}", icon_b64)
        # 把内联后的 HTML 写到 /tmp 临时文件，用 file:// 加载：
        # 1) 文档 origin 是 file://，JS 错误不会被 WebKit 跨源脱敏（之前 "Script error. @ ?:0:0" 就是这原因）
        # 2) 文件名带 pid+ts 每次都不同，根除 WebKit 主文档缓存
        # 3) 启动时清理旧的 mycodex_*.html，避免 /tmp 堆积
        for old in glob.glob("/tmp/mycodex_*.html"):
            try:
                if os.path.getmtime(old) < time.time() - 3600:
                    os.remove(old)
            except Exception:
                pass
        tmp_html = f"/tmp/mycodex_{os.getpid()}_{int(time.time()*1000)}.html"
        Path(tmp_html).write_text(html, encoding="utf-8")
        log("main: tmp html = " + tmp_html)
        webview.create_window(
            APP_NAME,
            url="file://" + tmp_html,
            js_api=api,
            width=1120,
            height=740,
            min_size=(920, 600),
        )
        log("main: window created, starting webview")
        webview.start()
        log("main: webview exited")
    except Exception as e:
        tb = traceback.format_exc()
        log("main EXCEPTION:\n" + tb)
        log_error(e, "main")
        raise


if __name__ == "__main__":
    main()
