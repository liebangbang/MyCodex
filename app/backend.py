# -*- coding: utf-8 -*-
"""
MyCode 后端（pywebview 版）——接入 DeepSeek API，承接编码代理逻辑。

本文件只负责「大脑」：流式对话、工具执行、会话存档、思考档判定。
所有界面交互通过 call_js() 把数据推给前端 HTML/JS。
"""

import os
import sys
import json
import re
import time
import threading
from pathlib import Path
from urllib.request import Request, urlopen

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


# --------------------------------------------------------------------------
# 配置与常量
# --------------------------------------------------------------------------
APP_NAME = "MyCode"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
CONFIG_DIR = Path.home() / ".config" / "mycode"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_DIR = CONFIG_DIR / "sessions"

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
MAX_TURNS = 25
MAX_TOKENS = 8192

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
    }
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for k in ("api_key", "model", "base_url"):
                if data.get(k):
                    cfg[k] = data[k]
        except Exception as e:
            log("读取配置文件失败：" + str(e))
    return cfg


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
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd),
            capture_output=True, text=True, timeout=120, errors="replace",
        )
        out = proc.stdout or ""
        if proc.stderr:
            out += (("\n--- stderr ---\n" + proc.stderr) if out else proc.stderr)
        out = _truncate(out, CMD_OUTPUT_LIMIT)
        return out + f"\n[exit code: {proc.returncode}]"
    except subprocess.TimeoutExpired:
        return "[超时] 命令执行超过 120 秒被终止。"
    except Exception as e:
        return f"执行错误：{e}"


TOOL_IMPL = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "list_dir": tool_list_dir,
    "grep_files": tool_grep_files,
}


# --------------------------------------------------------------------------
# DeepSeek 流式调用
# --------------------------------------------------------------------------
def stream_chat(messages, cfg, tools, thinking, max_tokens, on_text=None, on_reason=None):
    body = {
        "model": cfg["model"],
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
        "max_tokens": max_tokens,
    }
    if thinking:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "high"

    data = json.dumps(body).encode("utf-8")
    req = Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    content = []
    reasoning = []
    tc_acc = {}

    with urlopen(req, timeout=600) as resp:
        buf = ""
        for raw in resp:
            if not raw:
                continue
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
                    try:
                        chunk = json.loads(payload)
                    except Exception:
                        continue
                    _handle_chunk(chunk, content, reasoning, tc_acc, on_text, on_reason)

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
    return "".join(content), "".join(reasoning), tool_calls


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
        "你是 MyCode，一个 macOS 应用里的编码代理，底层由 DeepSeek 模型驱动。\n\n"
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
        self._stop = False
        self._asst_open = False
        self._asst_text = ""
        self._pending_confirm = None

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
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
            "sessions": self.list_sessions(),
            "need_key": not bool(self.cfg.get("api_key")),
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
                })
        out.sort(key=lambda x: x["updated"], reverse=True)
        return out

    # -------- 任务管理 --------
    def _unique_name(self, base):
        name = base
        i = 2
        while (SESSIONS_DIR / f"{name}.json").exists():
            name = f"{base} {i}"
            i += 1
        return name

    def new_task(self, name=None):
        base = (name or "任务").strip() or "任务"
        self.session_name = self._unique_name(base)
        self.history = []
        self.transcript = []
        self.artifacts = []
        self._save()
        return self._state()

    def open_task(self, name):
        p = SESSIONS_DIR / f"{name}.json"
        if not p.exists():
            return {"error": "not_found"}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": str(e)}
        self.session_name = name
        self.history = data.get("history", [])
        self.transcript = data.get("transcript", [])
        self.artifacts = data.get("artifacts", [])
        self.model = data.get("model", self.model)
        self.think_mode = data.get("think_mode", self.think_mode)
        cwd = data.get("cwd")
        if cwd and Path(cwd).exists():
            self.cwd = Path(cwd)
        return {
            "transcript": self.transcript,
            "artifacts": self.artifacts,
            "model": self.model,
            "think_mode": self.think_mode,
            "cwd": str(self.cwd),
            "cwd_short": _short_cwd(self.cwd),
            "name": name,
        }

    def _save(self):
        if not self.session_name:
            return
        data = {
            "name": self.session_name,
            "model": self.model,
            "think_mode": self.think_mode,
            "cwd": str(self.cwd),
            "updated": time.time(),
            "history": self.history,
            "transcript": self.transcript,
            "artifacts": self.artifacts,
        }
        p = SESSIONS_DIR / f"{self.session_name}.json"
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
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

    def preview(self, path):
        try:
            p = Path(path)
            if not p.exists() or p.is_dir():
                return {"error": "文件不存在或不是普通文件"}
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > PREVIEW_LIMIT:
                text = text[:PREVIEW_LIMIT] + "\n…（预览已截断）"
            return {"name": p.name, "content": text, "size": p.stat().st_size}
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
        self._ensure_assistant()
        self.call_js("appendReason", r)

    # -------- 工具执行 --------
    def add_artifact(self, path):
        p = Path(path)
        try:
            st = p.stat()
            size = st.st_size
            mtime = st.st_mtime
        except Exception:
            return
        entry = {"path": str(p), "name": p.name, "size": size, "mtime": mtime, "size_str": _fmt_size(size)}
        for i, a in enumerate(self.artifacts):
            if a["path"] == str(p):
                self.artifacts[i] = entry
                self.call_js("updateArtifact", entry)
                return
        self.artifacts.append(entry)
        self.call_js("addArtifact", entry)

    def _approve(self, command):
        self._pending_confirm = {"event": threading.Event(), "result": False}
        self.call_js("showConfirm", command)
        self._pending_confirm["event"].wait(timeout=300)
        res = self._pending_confirm["result"]
        self._pending_confirm = None
        return res

    def _run_tool(self, tc):
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
            result = TOOL_IMPL[name](args, self.cwd)
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
        self.transcript.append({"role": "tool", "name": name, "text": f"{name}({summary})\n{display}"})
        return str(result)

    # -------- 主循环 --------
    def send(self, text):
        if not text or not text.strip():
            return "empty"
        text = text.strip()
        if not self.cfg.get("api_key"):
            self.call_js("appendSystem", "未配置 API Key，请在 ~/.config/mycode/config.json 设置。")
            return "no_key"
        if not self.session_name:
            self.new_task()
        # 独立线程跑 agent loop，避免阻塞 pywebview 的 API 线程池
        # （否则危险命令确认弹窗会因线程被占而卡死）
        threading.Thread(target=self._run_agent, args=(text,), daemon=True).start()
        return "ok"

    def _run_agent(self, text):
        self._stop = False
        self._asst_open = False
        self.call_js("setBusy", True)
        self.call_js("appendUser", text)
        self.transcript.append({"role": "user", "text": text})
        try:
            self._agent_loop(text)
        finally:
            self.call_js("setBusy", False)
            self._save()
            self.call_js("afterSend")

    def _agent_loop(self, user_input):
        self._stop = False
        messages = [
            {"role": "system", "content": build_system_prompt(self.cwd)},
        ] + self.history + [
            {"role": "user", "content": user_input},
        ]
        thinking = resolve_think(self.think_mode, user_input)

        try:
            for _ in range(MAX_TURNS):
                content, reasoning, tool_calls = stream_chat(
                    messages, self.cfg, TOOLS, thinking, MAX_TOKENS,
                    on_text=self._on_text,
                )
                assistant_msg = {"role": "assistant", "content": content or None}
                if tool_calls:
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
                    self.history.append(assistant_msg)
                    if content:
                        self.transcript.append({"role": "assistant", "text": content})
                    for tc in tool_calls:
                        result = self._run_tool(tc)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })
                        self.history.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })
                else:
                    messages.append(assistant_msg)
                    self.history.append(assistant_msg)
                    if content:
                        self.transcript.append({"role": "assistant", "text": content})
                    self.call_js("appendSystem", "完成")
                    break
            else:
                self.call_js("appendSystem", "达到最大轮数")
        except InterruptedError:
            self.call_js("appendSystem", "已停止")
        except Exception as e:
            self.call_js("appendSystem", f"错误：{e}")
        finally:
            self._asst_open = False

    def _think_label(self):
        return {"off": "关", "auto": "自动", "on": "开"}.get(self.think_mode, "自动")


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def main():
    try:
        import traceback
        log("main: importing webview ok")
        api = Api()
        here = Path(__file__).resolve().parent
        index = here / "index.html"
        log("main: index path = " + str(index))
        webview.create_window(
            APP_NAME,
            str(index),
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
        raise


if __name__ == "__main__":
    main()
