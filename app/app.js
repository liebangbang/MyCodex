// MyCodex 前端逻辑：三栏布局 + 流式对话 + 产物预览
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const messagesEl = $("messages");
  const taskListEl = $("taskList");
  const artListEl = $("artList");
  const previewEl = $("preview");
  const inputEl = $("input");
  const statusEl = $("statusText");
  const cwdEl = $("cwdText");
  const modelSel = $("modelSel");
  const thinkSel = $("thinkSel");
  const artCountEl = $("artCount");

  // ---------------- 主题（亮/暗，跟随系统 + 手动切换 + 记忆） ----------------
  const themeKey = "mycodex_theme"; // "light" | "dark" | "auto"
  const media = window.matchMedia("(prefers-color-scheme: dark)");

  // 主题图标（线性 SVG，避免 emoji 渲染差异）
  const ICON_SUN = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  const ICON_MOON = '<svg class="icon" viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

  function applyTheme(mode) {
    const dark = mode === "dark" || (mode === "auto" && media.matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    const btn = $("btnTheme");
    if (btn) {
      btn.innerHTML = dark ? ICON_MOON : ICON_SUN;
      btn.title = "切换主题（当前：" + (dark ? "暗色" : "亮色") + "）";
      btn.classList.toggle("active", mode === "auto");
    }
  }

  function cycleTheme() {
    const cur = localStorage.getItem(themeKey) || "auto";
    const next = cur === "auto" ? "light" : cur === "light" ? "dark" : "auto";
    localStorage.setItem(themeKey, next);
    applyTheme(next);
  }

  function initTheme() {
    const mode = localStorage.getItem(themeKey) || "auto";
    applyTheme(mode);
    const btn = $("btnTheme");
    if (btn) btn.addEventListener("click", cycleTheme);
    try { media.addEventListener("change", () => applyTheme(localStorage.getItem(themeKey) || "auto")); } catch (e) {}
  }

  let currentAssistantEl = null;
  let currentAssistantRaw = "";
  let currentToolCard = null;
  let busy = false;
  let activeTask = null;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // 简易 Markdown：围栏代码块 + 行内代码
  function renderMarkdown(raw) {
    const parts = String(raw).split(/```/);
    let html = "";
    for (let i = 0; i < parts.length; i++) {
      if (i % 2 === 1) {
        let code = parts[i];
        const nl = code.indexOf("\n");
        let body = code;
        if (nl > -1) {
          const first = code.slice(0, nl).trim();
          if (/^[a-zA-Z0-9+#-]{1,20}$/.test(first)) body = code.slice(nl + 1);
        }
        html += '<pre class="code"><code>' + esc(body.replace(/\n$/, "")) + "</code></pre>";
      } else {
        let seg = esc(parts[i]).replace(/`([^`]+)`/g, '<code class="inline">$1</code>');
        html += seg;
      }
    }
    return html;
  }

  function scrollDown() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // 完成当前助手气泡的 Markdown 渲染
  function finalizeAssistant() {
    if (currentAssistantEl) {
      const content = currentAssistantEl.querySelector(".bubble");
      if (content) content.innerHTML = renderMarkdown(currentAssistantRaw);
      const text = currentAssistantRaw;
      const m = currentAssistantEl;
      // 给每条回答加“复制”按钮（点击复制该条纯文本）
      if (!m.querySelector(".copy-btn")) {
        const btn = document.createElement("button");
        btn.className = "copy-btn";
        btn.textContent = "复制";
        btn.addEventListener("click", () => copyText(text, (ok) => {
          btn.textContent = ok ? "已复制" : "复制失败";
          btn.classList.toggle("copied", !!ok);
          setTimeout(() => {
            btn.textContent = "复制";
            btn.classList.remove("copied");
          }, 1500);
        }));
        m.appendChild(btn);
      }
      currentAssistantEl = null;
      currentAssistantRaw = "";
    }
  }

  // ---------------- 复制（三层兜底） ----------------
  // 1) Clipboard API → 2) pywebview 桥接(NSPasteboard) → 3) execCommand 兼容
  function fallbackCopyText(text) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "0";
      ta.style.left = "0";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, text.length);
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return !!ok;
    } catch (e) {
      return false;
    }
  }

  function copyViaBridge(text, done) {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.copy_text) {
      window.pywebview.api.copy_text(text).then(
        (res) => done(res === "ok"),
        () => done(fallbackCopyText(text))
      );
    } else {
      done(fallbackCopyText(text));
    }
  }

  function copyText(text, done) {
    if (!text) { done && done(false); return; }
    const finish = (ok) => done && done(ok);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => finish(true),
        () => copyViaBridge(text, finish)
      );
    } else {
      copyViaBridge(text, finish);
    }
  }

  // ---------------- 消息渲染 ----------------
  // 本次发送的图片缩略图（doSend 时保存，appendUser 时消费展示；重放无缩略图则用数量徽章）
  let _pendingThumbs = [];
  function appendUser(text, imgCount) {
    finalizeAssistant();
    const m = document.createElement("div");
    m.className = "msg user";
    const b = document.createElement("div");
    b.className = "bubble";
    let inner = "";
    if (text) {
      inner += '<div class="user-text">' + esc(text).replace(/\n/g, "<br>") + "</div>";
    }
    if (imgCount) {
      const thumbs = _pendingThumbs;
      _pendingThumbs = [];
      if (thumbs.length) {
        inner += '<div class="user-thumbs">' + thumbs.map((th) => {
          if (th.kind === "pdf") {
            return '<span class="user-thumb file">PDF</span>';
          }
          return '<img class="user-thumb" src="' + th.dataUrl + '" alt="图片" />';
        }).join("") + "</div>";
      } else {
        inner += '<div class="user-img-badge">🖼️ 附 ' + imgCount + " 张图片/文档</div>";
      }
    }
    if (!inner) inner = "（空消息）";
    b.innerHTML = inner;
    m.appendChild(b);
    messagesEl.appendChild(m);
    scrollDown();
  }

  function beginAssistant() {
    finalizeAssistant();
    const m = document.createElement("div");
    m.className = "msg assistant";
    const label = document.createElement("div");
    label.className = "msg-label";
    label.textContent = "MyCodex";
    const b = document.createElement("div");
    b.className = "bubble";
    m.appendChild(label);
    m.appendChild(b);
    messagesEl.appendChild(m);
    currentAssistantEl = m;
    currentAssistantRaw = "";
    scrollDown();
  }

  function appendAssistant(t) {
    if (!currentAssistantEl) beginAssistant();
    currentAssistantRaw += t;
    const b = currentAssistantEl.querySelector(".bubble");
    b.appendChild(document.createTextNode(t));
    scrollDown();
  }

  function appendReason(r) {
    // 不在界面展示思考过程，直接丢弃
  }

  function appendSystem(msg) {
    finalizeAssistant();
    const s = document.createElement("div");
    s.className = "sys-line";
    s.textContent = msg;
    messagesEl.appendChild(s);
    scrollDown();
  }

  // ---------- 工具调用：紧凑单行卡片 ----------
  // 智能提取关键参数：write_file→文件名 / run_command→命令 / read_file→路径 …
  function toolBasename(p) { return String(p == null ? "" : p).split("/").pop(); }
  function smartToolArgs(name, summary) {
    let s = String(summary == null ? "" : summary).trim();
    if (!s) return "";
    let obj = null;
    try { obj = JSON.parse(s); } catch (e) {}
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      if (name === "write_file" || name === "edit_file") s = "→ " + toolBasename(obj.path);
      else if (name === "read_file") s = "→ " + toolBasename(obj.path);
      else if (name === "run_command") s = "$ " + String(obj.command || "").slice(0, 60);
      else if (name === "list_dir") s = "→ " + (toolBasename(obj.path) || obj.path || "");
      else if (name === "grep_files") s = "/" + (obj.pattern || "") + "/ → " + (toolBasename(obj.path) || obj.path || "");
      else {
        const keys = Object.keys(obj).slice(0, 2);
        s = keys.map(k => k + "=" + String(obj[k]).slice(0, 30)).join(" ") + (Object.keys(obj).length > 2 ? " …" : "");
        if (s.length > 80) s = s.slice(0, 80) + " …";
      }
    } else {
      if (s.length > 60) s = s.slice(0, 60) + " …";
    }
    return s;
  }

  function appendTool(name, summary) {
    finalizeAssistant();
    const card = document.createElement("div");
    card.className = "tool-card";
    const args = smartToolArgs(name, summary);
    card.innerHTML =
      '<div class="tool-head"><span class="tool-dot"></span>' +
      '<span class="tool-name">' + esc(name) + "</span>" +
      (args ? '<span class="tool-args">' + esc(args) + "</span>" : "") +
      '<span class="tool-status" style="display:none"></span>' +
      '<svg class="tool-caret" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>' +
      "</div>" +
      '<div class="tool-result" style="display:none"></div>';
    messagesEl.appendChild(card);
    // 点击展开/收起完整结果
    card.addEventListener("click", () => {
      const r = card.querySelector(".tool-result");
      const open = r.style.display !== "none";
      r.style.display = open ? "none" : "block";
      card.classList.toggle("open", !open);
      scrollDown();
    });
    currentToolCard = card;
    scrollDown();
  }

  function appendToolResult(name, summary, result) {
    const card = currentToolCard || (function () { appendTool(name, summary); return currentToolCard; })();
    // 状态徽章：结果第一行，超长截断；完整结果存在 title 悬停可见
    const first = String(result || "").split("\n")[0].slice(0, 56);
    const st = card.querySelector(".tool-status");
    if (first.trim()) {
      st.style.display = "";
      st.textContent = "✓ " + first;
      st.title = String(result || "");
    } else {
      st.style.display = "";
      st.textContent = "✓";
    }
    const r = card.querySelector(".tool-result");
    r.textContent = result;
    const isErr = /error|failed|拒绝|失败|exit code: [1-9]|timeout|超时/i.test(String(result || "").slice(0, 300));
    st.classList.toggle("err", isErr);
    scrollDown();
  }

  // ---------------- 产物 ----------------
  function renderArtifacts(list) {
    artListEl.innerHTML = "";
    artCountEl.textContent = list.length;
    list.forEach((a) => artListEl.appendChild(makeArtItem(a)));
  }

  function openInBrowser(url) {
    if (!apiReady()) return;
    window.pywebview.api.open_url(url).then((res) => {
      if (res && res.error) appendSystem("无法打开链接：" + res.error);
    });
  }

  function makeArtItem(a) {
    const item = document.createElement("div");
    item.className = "art-item";
    item.dataset.path = a.path;
    const ext = (a.name.split(".").pop() || "").toLowerCase();
    let cls = "";
    let iconSvg = '<svg class="icon" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>';
    if (a.is_url) {
      cls = "url";
      iconSvg = '<svg class="icon" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
    } else if (ext === "md" || ext === "markdown") {
      cls = "green";
      iconSvg = '<svg class="icon" viewBox="0 0 24 24"><path d="M4 5h16v14H4z"/><path d="m8 15 2-2 2 2 3-3"/></svg>';
    } else if (ext === "html" || ext === "htm") {
      cls = "blue";
      iconSvg = '<svg class="icon" viewBox="0 0 24 24"><path d="M9 8 5 12l4 4"/><path d="m15 8 4 4-4 4"/><path d="m13 5-2 14"/></svg>';
    } else if (ext === "png" || ext === "jpg" || ext === "jpeg" || ext === "gif" || ext === "webp" || ext === "svg" || ext === "bmp") {
      cls = "pink";
      iconSvg = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/><path d="m21 15-5-5-9 9"/></svg>';
    } else if (ext === "pdf") {
      cls = "red";
      iconSvg = '<svg class="icon" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M9 13h6M9 16.5h4"/></svg>';
    } else if (ext === "json" || ext === "yml" || ext === "yaml") {
      cls = "amber";
    }
    const icon = document.createElement("div");
    icon.className = "art-icon " + cls;
    icon.innerHTML = iconSvg;
    const meta = document.createElement("div");
    meta.className = "art-meta";
    meta.innerHTML =
      '<div class="art-name">' + esc(a.name) + "</div>" +
      '<div class="art-sub">' + (a.is_url ? esc(a.url || "链接") : esc(a.size_str || (a.size + "B"))) + "</div>";
    item.appendChild(icon);
    item.appendChild(meta);

    // 链接 / 图片 / 网页 / PDF 提供“在浏览器打开”
    const openable = a.is_url || ["md", "markdown", "html", "htm", "pdf", "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].indexOf(ext) > -1;
    if (openable) {
      const openBtn = document.createElement("button");
      openBtn.className = "icon-btn art-open";
      openBtn.title = "在浏览器打开";
      openBtn.innerHTML = '<svg class="icon" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="m21 3-7 7"/></svg>';
      openBtn.onclick = (e) => {
        e.stopPropagation();
        if (a.is_url) openInBrowser(a.url || a.path);
        else if (apiReady()) window.pywebview.api.open_external(a.path);
      };
      item.appendChild(openBtn);
    }

    item.onclick = () => {
      document.querySelectorAll(".art-item").forEach((x) => x.classList.remove("active"));
      item.classList.add("active");
      previewArtifact(a.path);
    };
    return item;
  }

  function addArtifact(a) {
    // 去重
    const existing = artListEl.querySelector('[data-path="' + cssEsc(a.path) + '"]');
    if (existing) {
      existing.replaceWith(makeArtItem(a));
    } else {
      artListEl.insertBefore(makeArtItem(a), artListEl.firstChild);
    }
    artCountEl.textContent = artListEl.children.length;
  }
  function updateArtifact(a) { addArtifact(a); }

  function cssEsc(s) { return s.replace(/["\\]/g, "\\$&"); }

  function previewArtifact(path) {
    if (!apiReady()) return;
    window.pywebview.api.preview(path).then((res) => {
      if (res.error) {
        previewEl.innerHTML = '<div class="preview-empty">' + esc(res.error) + "</div>";
        return;
      }
      renderPreview(res);
      previewEl.scrollTop = 0;
    });
  }

  // 当前预览视图模式："source"（源码）| "rendered"（渲染）
  // HTML 默认源码（避免自身递归 / 复杂页面乱码），MD 默认渲染
  let _previewView = "source";
  let _previewing = null;  // 当前预览的产物对象（切换视图时复用）

  // 检测 MyCodex 自身的 HTML：含任务面板 + 消息区两个特征 id 即视为 app 自身源码
  function isSelfRefHtml(a) {
    if (a.kind !== "html") return false;
    const c = a.content || "";
    return /id="taskList"/.test(c) && /id="messages"/.test(c);
  }

  function previewToolbar(a) {
    // 预览区右上角按钮：源码↔渲染切换 + 在浏览器打开
    let html = "";
    if (a.kind === "html") {
      // MyCodex 自身 HTML：禁用渲染切换（递归会乱码），仅显示"在浏览器打开"
      if (!isSelfRefHtml(a)) {
        const isSource = _previewView === "source";
        html += '<button class="btn ghost small view-toggle" data-act="view" title="切换源码/渲染视图">' +
          (isSource
            ? '<svg class="icon" viewBox="0 0 24 24"><path d="m21 3-7 7"/><path d="m21 3-5 11-3-4-4-3z"/><path d="M14 3h7v7"/></svg>渲染'
            : '<svg class="icon" viewBox="0 0 24 24"><path d="m16 18 6-6"/><path d="m8 6 6 6"/><path d="m2 12 6 6"/><path d="m16 6 6 6"/><path d="m8 18 6-6"/></svg>源码') +
          '</button>';
      }
    } else if (a.kind === "md") {
      const isSource = _previewView === "source";
      html += '<button class="btn ghost small view-toggle" data-act="view" title="切换源码/渲染视图">' +
        (isSource ? '渲染' : '源码') + '</button>';
    }
    if (a.kind !== "text" && a.kind !== "binary") {
      html += '<button class="btn ghost small open-btn" data-act="open">' +
        '<svg class="icon" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="m21 3-7 7"/></svg>' +
        "在浏览器打开</button>";
    }
    return html;
  }

  function renderPreview(a) {
    // 新文件预览：HTML 走源码、MD 走渲染（更安全稳定）
    if (a.kind === "html") _previewView = "source";
    else if (a.kind === "md") _previewView = "rendered";
    _previewing = a;
    const selfRef = isSelfRefHtml(a);
    const titleSuffix = selfRef ? ' · <span class="preview-hint-inline">MyCodex 自身源码（仅源码）</span>' : "";
    const head =
      '<div class="preview-head">' +
        '<span class="preview-title">' + esc(a.name) + "  ·  " +
          (a.kind === "image" ? "图片" : a.kind === "html" ? "网页" : a.kind === "md" ? "Markdown" :
           a.kind === "pdf" ? "PDF 文档" : a.kind === "binary" ? "文件" : esc(a.size_str || String(a.size) + "B")) +
          titleSuffix +
        "</span>" +
        previewToolbar(a) +
      "</div>";
    previewEl.innerHTML = head + renderPreviewBody(a);
    // 绑定所有按钮
    previewEl.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const act = btn.dataset.act;
        if (act === "url") openInBrowser(a.url || a.content);
        else if (act === "view") togglePreviewView();
        else if (apiReady()) window.pywebview.api.open_external(a.path);
      });
    });
  }

  function renderPreviewBody(a) {
    const view = _previewView;
    if (a.kind === "image") {
      return '<img class="preview-img" src="' + a.data_url + '" alt="' + esc(a.name) + '" />';
    } else if (a.kind === "html") {
      if (view === "rendered") {
        return '<iframe class="preview-frame" sandbox="allow-same-origin allow-scripts" srcdoc="' + esc(a.content) + '"></iframe>';
      }
      return '<pre class="preview-content">' + esc(a.content) + "</pre>";
    } else if (a.kind === "md") {
      if (view === "source") {
        return '<pre class="preview-content">' + esc(a.content) + "</pre>";
      }
      return '<div class="preview-md">' + renderMarkdownFull(a.content) + "</div>";
    } else if (a.kind === "pdf") {
      return '<div class="preview-file">' +
        '<svg class="icon big" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>' +
        "<div>PDF 文档</div>" +
        '<div class="preview-hint">' + esc(a.hint || "点击下方按钮在浏览器中打开") + "</div>" +
        '<button class="btn primary" data-act="open">在浏览器打开</button>' +
      "</div>";
    } else if (a.kind === "binary") {
      return '<div class="preview-file">' +
        '<svg class="icon big" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>' +
        "<div>二进制文件</div>" +
        '<div class="preview-hint">' + esc(a.hint || "无法直接预览，请在浏览器/默认应用中打开") + "</div>" +
        '<button class="btn primary" data-act="open">用系统应用打开</button>' +
      "</div>";
    } else if (a.is_url) {
      return
        '<div class="url-card">' +
          '<div class="url-row">' +
            '<svg class="icon" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>' +
            '<span class="url-text">' + esc(a.url || a.content) + "</span>" +
          "</div>" +
          '<button class="btn primary" data-act="url">在浏览器打开</button>' +
        "</div>";
    }
    return '<pre class="preview-content">' + esc(a.content) + "</pre>";
  }

  function togglePreviewView() {
    if (!_previewing) return;
    _previewView = _previewView === "source" ? "rendered" : "source";
    // 替换 body 与 toolbar（head 标题保持）
    const head = previewEl.querySelector(".preview-head");
    if (!head) return;
    const newBody = renderPreviewBody(_previewing);
    // 替换 head 内的 toolbar（title 保留，按钮更新）
    const newToolbar = previewToolbar(_previewing);
    // 先清掉预览体（除 head 之外）
    Array.from(previewEl.children).forEach((c) => { if (c !== head) c.remove(); });
    // 包装 body 元素以便 toggle 后替换
    const wrap = document.createElement("div");
    wrap.className = "preview-body";
    wrap.innerHTML = newBody;
    previewEl.appendChild(wrap);
    // 更新 head 里的按钮
    const oldTitle = head.querySelector(".preview-title");
    head.innerHTML = "";
    head.appendChild(oldTitle);
    head.insertAdjacentHTML("beforeend", newToolbar);
    // 重新绑定 head 按钮
    head.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const act = btn.dataset.act;
        const a = _previewing;
        if (act === "url") openInBrowser(a.url || a.content);
        else if (act === "view") togglePreviewView();
        else if (apiReady()) window.pywebview.api.open_external(a.path);
      });
    });
    previewEl.scrollTop = 0;
  }

  // Markdown 完整渲染（预览区用）：标题 / 列表 / 引用 / 加粗 / 链接 / 行内代码 / 代码块
  function renderMarkdownFull(raw) {
    const src = String(raw == null ? "" : raw);
    // 先处理代码块，占位保护
    const blocks = [];
    let text = src.replace(/```([\s\S]*?)```/g, (m, code) => {
      blocks.push('<pre class="md-code"><code>' + esc(code.replace(/^\n/, "").replace(/\n$/, "")) + "</code></pre>");
      return "\u0000MD" + (blocks.length - 1) + "\u0000";
    });
    const lines = text.split("\n");
    const html = [];
    let inList = false, inQuote = false;
    const closeList = () => { if (inList) { html.push("</ul>"); inList = false; } };
    const closeQuote = () => { if (inQuote) { html.push("</blockquote>"); inQuote = false; } };
    for (let line of lines) {
      const inline = (s) => s
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/`([^`]+)`/g, '<code class="md-inline">$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) { closeList(); closeQuote(); html.push("<h" + h[1].length + ">" + inline(h[2]) + "</h" + h[1].length + ">"); continue; }
      if (/^\s*[-*+]\s+/.test(line)) {
        if (!inList) { closeQuote(); html.push("<ul>"); inList = true; }
        html.push("<li>" + inline(line.replace(/^\s*[-*+]\s+/, "")) + "</li>"); continue;
      }
      if (/^\s*>\s?/.test(line)) {
        if (!inQuote) { closeList(); html.push("<blockquote>"); inQuote = true; }
        html.push(inline(line.replace(/^\s*>\s?/, ""))); continue;
      }
      closeList(); closeQuote();
      if (!line.trim()) { html.push(""); continue; }
      html.push("<p>" + inline(line) + "</p>");
    }
    closeList(); closeQuote();
    let out = html.join("\n");
    out = out.replace(/\u0000MD(\d+)\u0000/g, (m, i) => blocks[+i]);
    return out;
  }

  // ---------------- 任务列表 ----------------
  function renderSessions(sessions) {
    taskListEl.innerHTML = "";
    // 按 parent 分组：先渲染根任务，再渲染其子任务（缩进）
    const byParent = {};
    sessions.forEach((s) => {
      const key = s.parent || "";
      (byParent[key] = byParent[key] || []).push(s);
    });
    const roots = byParent[""] || [];
    const rendered = new Set();
    roots.forEach((root) => {
      taskListEl.appendChild(makeTaskItem(root, 0));
      rendered.add(root.name);
      (byParent[root.name] || []).forEach((child) => {
        taskListEl.appendChild(makeTaskItem(child, 1));
        rendered.add(child.name);
      });
    });
    // 兜底：父任务已不存在（孤儿子任务）也作为根展示，避免丢失
    sessions.forEach((s) => {
      if (!rendered.has(s.name)) taskListEl.appendChild(makeTaskItem(s, 0));
    });
    highlightActive();
  }

  function makeTaskItem(s, depth) {
    const item = document.createElement("div");
    item.className = "task-item" + (s.name === activeTask ? " active" : "") + (s.pinned ? " pinned" : "");
    item.dataset.name = s.name;
    item.dataset.depth = depth;
    const date = new Date((s.updated || 0) * 1000);
    const rel = isNaN(date) ? "" : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const indent = depth > 0 ? ' style="padding-left:' + (26 + depth * 16) + 'px"' : "";
    item.innerHTML =
      '<div class="task-row">' +
        '<div class="task-main"' + indent + ">" +
          '<div class="task-text">' +
            '<div class="task-name">' + (depth > 0 ? "↳ " : "") + esc(s.name) + "</div>" +
            '<div class="task-meta">' +
              (depth > 0 ? "子任务 · " : "") +
              (s.msg_count || 0) + " 条 · " + esc(rel) +
            "</div>" +
          "</div>" +
        "</div>" +
        '<div class="task-actions">' +
          '<button class="task-btn" title="重命名" data-act="rename">' +
            '<svg class="icon" viewBox="0 0 24 24"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/></svg>' +
          "</button>" +
          '<button class="task-btn' + (s.pinned ? " pinned" : "") + '" title="' + (s.pinned ? "取消置顶" : "置顶") + '" data-act="pin">' +
            '<svg class="icon" viewBox="0 0 24 24"><path d="M12 17v5"/><path d="M9 4h6l-1 7 3 3H7l3-3z"/></svg>' +
          "</button>" +
          '<button class="task-btn" title="新建子任务" data-act="sub">' +
            '<svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>' +
          "</button>" +
          '<button class="task-btn danger" title="删除任务" data-act="del">' +
            '<svg class="icon" viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>' +
          "</button>" +
        "</div>" +
      "</div>";
    item.onclick = () => openTask(s.name);
    item.querySelectorAll(".task-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const act = btn.dataset.act;
        if (act === "rename") renameTask(s.name);
        else if (act === "pin") togglePin(s.name);
        else if (act === "sub") newSubtask(s.name);
        else if (act === "del") deleteTask(s.name);
      });
    });
    return item;
  }

  function renameTask(name) {
    const newName = window.prompt("重命名任务：", name);
    if (newName === null) return;
    const v = newName.trim();
    if (!v || v === name) return;
    if (!apiReady()) return;
    window.pywebview.api.rename_task(name, v).then((res) => {
      if (res.error) { appendSystem("重命名失败：" + res.error); return; }
      if (activeTask === name) {
        activeTask = res.name;
        setHeader({ session_name: res.name });
      }
      refreshSessions();
      highlightActive();
    });
  }

  function togglePin(name) {
    if (!apiReady()) return;
    window.pywebview.api.toggle_pin(name).then((res) => {
      if (res.error) { appendSystem("操作失败：" + res.error); return; }
      refreshSessions();
      highlightActive();
    });
  }

  function newSubtask(parent) {
    const name = window.prompt("子任务名称（可留空，自动命名）：", "");
    if (name === null) return;
    if (!apiReady()) return;
    window.pywebview.api.new_subtask(parent, name.trim()).then((res) => {
      if (res.error) { appendSystem("创建子任务失败：" + res.error); return; }
      openTask(res.name);
    });
  }

  function deleteTask(name) {
    // 统计直接子任务数量，删除时提示会级联删除
    const children = (window._sessionsCache || []).filter((s) => s.parent === name).length;
    const msg = children > 0
      ? `确认删除任务「${name}」？\n该任务下有 ${children} 个子任务，将一并删除。`
      : `确认删除任务「${name}」？\n此操作不可撤销。`;
    if (!window.confirm(msg)) return;
    if (!apiReady()) return;
    window.pywebview.api.delete_task(name).then((res) => {
      if (res.error) { appendSystem("删除失败：" + res.error); return; }
      // 若删除的是当前打开的任务，清空界面
      if (activeTask === name) {
        activeTask = null;
        clearMessages();
        setHeader({});
        renderArtifacts([]);
        inputEl.focus();
      }
      refreshSessions();
      highlightActive();
    });
  }

  function openTask(name) {
    if (!apiReady()) return;
    window.pywebview.api.open_task(name).then((res) => {
      if (res.error) return;
      activeTask = name;
      clearMessages();
      (res.transcript || []).forEach(playTranscript);
      renderArtifacts(res.artifacts || []);
      setHeader({ session_name: res.name, model: res.model, think_mode: res.think_mode, cwd_short: res.cwd_short });
      renderSessions(window._sessionsCache || []);
      highlightActive();
    });
  }

  function playTranscript(entry) {
    if (entry.role === "user") {
      appendUser(entry.text || "", entry.image);
    }
    else if (entry.role === "assistant") { beginAssistant(); appendAssistant(entry.text || ""); finalizeAssistant(); }
    else if (entry.role === "tool") {
      const m = (entry.text || "").match(/^(\w+)\(([\s\S]*?)\)\n([\s\S]*)$/);
      const name = m ? m[1] : (entry.name || "tool");
      const rest = m ? m[3] : entry.text;
      appendTool(name, "", "");
      appendToolResult(name, "", rest || "");
    }
  }

  function highlightActive() {
    document.querySelectorAll(".task-item").forEach((x) => {
      x.classList.toggle("active", x.dataset.name === activeTask);
    });
  }

  function clearMessages() {
    messagesEl.innerHTML = "";
    currentAssistantEl = null;
    currentAssistantRaw = "";
    currentToolCard = null;
  }

  // ---------------- 头部 / 状态 ----------------
  function setHeader(st) {
    if (st.session_name) {
      activeTask = st.session_name;
      $("taskTitle").textContent = st.session_name;
      $("centerTitle").textContent = st.session_name;
    } else {
      $("taskTitle").textContent = "未命名任务";
      $("centerTitle").textContent = "对话框";
    }
    if (st.model) modelSel.value = st.model;
    if (st.think_mode) thinkSel.value = st.think_mode;
    cwdEl.textContent = st.cwd_short ? "📁 " + st.cwd_short : "";
  }

  function setBusy(b) {
    busy = b;
    $("btnSend").disabled = b;
    $("btnStop").style.display = b ? "" : "none";
    statusEl.textContent = b ? "思考中…" : "";
    statusEl.classList.toggle("busy", b);
  }

  function applyState(st) {
    const prevTask = activeTask;  // 在 setHeader 修改前捕获，供下方恢复判断使用
    setHeader(st);
    window._visionReady = !!st.vision_ok;
    renderSessions(st.sessions || []);
    window._sessionsCache = st.sessions || [];
    renderArtifacts(st.artifacts || []);
    // 启动时若已自动恢复上次任务，渲染其历史对话
    if (st.session_name && st.transcript && st.transcript.length && prevTask !== st.session_name) {
      activeTask = st.session_name;
      clearMessages();
      st.transcript.forEach(playTranscript);
      renderArtifacts(st.artifacts || []);
      highlightActive();
    }
    if (st.need_key) {
      appendSystem("未配置 API Key，请在 ~/.config/mycode/config.json 设置 api_key。");
    } else if (st.vision_ok === false) {
      appendSystem("图片/PDF 将用本地 OCR 提取文字后由 AI 理解（免费、纯本地）。如需看懂画面/图表本身，可在 config.json 配置 vision_api_key 启用视觉模型。");
    }
  }

  function refreshSessions() {
    if (!apiReady()) return;
    window.pywebview.api.list_sessions().then((s) => {
      window._sessionsCache = s;
      renderSessions(s);
      highlightActive();
    });
  }

  // ---------------- 发送 ----------------
  function doSend() {
    const text = inputEl.value.trim();
    const images = pendingImages.map((x) => x.dataUrl);
    if ((!text && !images.length) || busy || !apiReady()) return;
    _pendingThumbs = pendingImages.map((x) => ({ dataUrl: x.dataUrl, kind: x.kind }));
    inputEl.value = "";
    clearImgPreview();
    autoResize();
    setBusy(true);
    window.pywebview.api.send(text, images).then(() => {
      window.pywebview.api.init().then((st) => { applyState(st); highlightActive(); });
    }).catch((e) => { setBusy(false); appendSystem("发送失败：" + e); });
  }

  function autoResize() {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + "px";
  }

  function apiReady() {
    return window.pywebview && window.pywebview.api;
  }

  // ---------------- 图片/文档输入（多模态，支持多张） ----------------
  const fileImgEl = $("fileImg");
  const imgPreviewEl = $("imgPreview");
  const imgThumbsEl = $("imgThumbs");
  const imgHintEl = $("imgHint");
  let pendingImages = [];           // [{dataUrl, name, kind}]  kind: "image" | "pdf"
  let ocrRunning = false;

  const PDF_ICON = "data:image/svg+xml;base64," + btoa(
    '<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#D9484F" stroke-width="1.6"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M8 13h8M8 16.5h5"/></svg>'
  );

  function addPendingImage(dataUrl, name, kind) {
    pendingImages.push({ dataUrl, name, kind });
    renderImgThumbs();
  }

  function renderImgThumbs() {
    imgThumbsEl.innerHTML = "";
    pendingImages.forEach((it, idx) => {
      const cell = document.createElement("div");
      cell.className = "img-thumb";
      const im = document.createElement("img");
      if (it.kind === "pdf") im.src = PDF_ICON; else im.src = it.dataUrl;
      cell.appendChild(im);
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = it.kind === "pdf" ? "PDF" : "图片";
      cell.appendChild(badge);
      const del = document.createElement("button");
      del.className = "del";
      del.textContent = "×";
      del.title = "移除这张";
      del.onclick = () => { pendingImages.splice(idx, 1); renderImgThumbs(); };
      cell.appendChild(del);
      imgThumbsEl.appendChild(cell);
    });
    if (pendingImages.length) {
      const visionReady = !!window._visionReady;
      imgHintEl.textContent = visionReady
        ? `已附 ${pendingImages.length} 个文件，将用视觉模型理解画面`
        : `已附 ${pendingImages.length} 个文件，将用本地 OCR 提取文字`;
      imgPreviewEl.style.display = "flex";
      imgPreviewEl.scrollIntoView({ block: "nearest" });
    } else {
      imgPreviewEl.style.display = "none";
    }
  }

  function clearImgPreview() {
    pendingImages = [];
    imgThumbsEl.innerHTML = "";
    imgPreviewEl.style.display = "none";
    fileImgEl.value = "";
  }

  function handleImageFile(file) {
    if (!file) return;
    const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name || "");
    if (isPdf) {
      const reader = new FileReader();
      reader.onload = () => addPendingImage(reader.result, file.name, "pdf");
      reader.onerror = () => appendSystem("读取 PDF 失败");
      reader.readAsDataURL(file);
      return;
    }
    if (!/^image\//.test(file.type || "")) {
      appendSystem("仅支持图片（PNG/JPG 等）或 PDF 文档");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => addPendingImage(reader.result, file.name, "image");
    reader.onerror = () => appendSystem("读取图片失败");
    reader.readAsDataURL(file);
  }

  // OCR：手动把图片文字提取进输入框（本地免费；不影响图片随消息发送）
  function runOcr() {
    if (!pendingImages.length) return;
    if (!apiReady()) return;
    if (ocrRunning) return;
    const imgs = pendingImages.filter((x) => x.kind === "image");
    if (!imgs.length) {
      appendSystem("OCR 仅支持图片；PDF 可直接发送给 AI 理解");
      return;
    }
    const btn = $("btnOcr");
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "识别中…";
    let done = 0;
    let collected = [];
    imgs.forEach((it, i) => {
      window.pywebview.api.ocr_image(it.dataUrl).then((res) => {
        if (res && res.text) collected.push((imgs.length > 1 ? `[图片${i + 1}]\n` : "") + res.text);
        else if (res && res.error) appendSystem("OCR 失败：" + res.error);
        done++;
        if (done === imgs.length) {
          btn.disabled = false;
          btn.textContent = old;
          const text = collected.join("\n\n").trim();
          if (!text) { appendSystem("未在图片中识别到文字"); return; }
          const prefix = inputEl.value.trim() ? "\n\n" : "";
          inputEl.value += prefix + "[图片OCR识别结果]\n" + text;
          autoResize();
          inputEl.focus();
        }
      }).catch((e) => {
        done++;
        appendSystem("OCR 调用失败：" + e);
        if (done === imgs.length) { btn.disabled = false; btn.textContent = old; }
      });
    });
  }

  $("btnImg").onclick = () => fileImgEl.click();
  fileImgEl.onchange = () => {
    if (fileImgEl.files) {
      for (const f of fileImgEl.files) handleImageFile(f);
    }
    fileImgEl.value = "";
  };
  $("btnImgCancel").onclick = clearImgPreview;
  $("btnOcr").onclick = () => { runOcr(); };

  // 粘贴图片（支持一次粘贴多张）
  document.addEventListener("paste", (e) => {
    if (!e.clipboardData || !e.clipboardData.items) return;
    const items = e.clipboardData.items;
    let added = false;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf("image/") === 0) {
        const file = items[i].getAsFile();
        if (file) {
          e.preventDefault();
          handleImageFile(file);
          added = true;
        }
      }
    }
    if (added) return;
  });

  // 拖拽图片到输入区（支持多文件）
  const composer = document.querySelector(".composer");
  composer.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  composer.addEventListener("drop", (e) => {
    e.preventDefault();
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) {
      for (const f of files) handleImageFile(f);
    }
  });

  // ---------------- 事件绑定 ----------------
  inputEl.addEventListener("input", autoResize);
  inputEl.addEventListener("keydown", (e) => {
    // 输入法组合输入中（如拼音选词），不拦截、不发送，避免误发
    if (e.isComposing || e.keyCode === 229) return;
    // ⌘/Ctrl + Enter 发送
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      doSend();
      return;
    }
    // 普通 Enter：默认换行（textarea 原生行为），不发送
  });
  $("btnSend").onclick = doSend;
  $("btnStop").onclick = () => { if (apiReady()) window.pywebview.api.stop(); };

  modelSel.onchange = () => { if (apiReady()) window.pywebview.api.set_model(modelSel.value); };
  thinkSel.onchange = () => { if (apiReady()) window.pywebview.api.set_think_mode(thinkSel.value); };

  $("btnDir").onclick = () => {
    if (!apiReady()) return;
    window.pywebview.api.choose_dir().then((cwd) => {
      cwdEl.textContent = "📁 " + cwd;
    });
  };
  // 右下角 cwd 路径也可点击切换目录
  cwdEl.style.cursor = "pointer";
  cwdEl.title = "点击切换工作目录";
  cwdEl.onclick = $("btnDir").onclick;

  function newTask() {
    const name = window.prompt("任务名称（可留空，自动命名）：", "");
    if (name === null) return;
    if (!apiReady()) return;
    window.pywebview.api.new_task(name).then((st) => {
      clearMessages();
      applyState(st);
      refreshSessions();
      highlightActive();
      inputEl.focus();
    });
  }
  $("btnNew").onclick = newTask;
  $("btnAddTask").onclick = newTask;

  // ---------------- 分隔条拖拽 ----------------
  function makeSplitter(el, side) {
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const body = document.querySelector(".body");
      const rect = body.getBoundingClientRect();
      const onMove = (ev) => {
        if (side === "left") {
          const w = Math.max(160, Math.min(ev.clientX - rect.left, 360));
          $("tasks").style.flex = "0 0 " + w + "px";
          $("tasks").style.width = w + "px";
        } else {
          const w = Math.max(200, Math.min(rect.right - ev.clientX, 440));
          $("artifacts").style.flex = "0 0 " + w + "px";
          $("artifacts").style.width = w + "px";
        }
      };
      const onUp = () => {
        el.classList.remove("active");
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      el.classList.add("active");
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  }
  makeSplitter($("splitLeft"), "left");
  makeSplitter($("splitRight"), "right");

  // ---------------- 危险命令确认弹窗 ----------------
  function showConfirm(command) {
    $("modalCmd").textContent = command;
    $("modalMask").style.display = "flex";
  }
  $("modalCancel").onclick = () => {
    $("modalMask").style.display = "none";
    if (apiReady()) window.pywebview.api.confirm_response(false);
  };
  $("modalOk").onclick = () => {
    $("modalMask").style.display = "none";
    if (apiReady()) window.pywebview.api.confirm_response(true);
  };

  // 暴露给后端调用的全局函数
  window.appendUser = appendUser;
  window.beginAssistant = beginAssistant;
  window.appendAssistant = appendAssistant;
  window.appendReason = appendReason;
  window.appendSystem = appendSystem;
  window.appendTool = appendTool;
  window.appendToolResult = appendToolResult;
  window.setBusy = setBusy;
  window.addArtifact = addArtifact;
  window.updateArtifact = updateArtifact;
  window.showConfirm = showConfirm;
  window.afterSend = function () {
    refreshSessions();
    highlightActive();
  };

  // ---------------- 启动（兼容 pywebviewready 时序坑 + API 方法异步注册） ----------------
  initTheme();
  function _bootstrap() {
    window._bsCount = (window._bsCount || 0) + 1;
    if (window._bsCount > 40) {
      appendSystem("pywebview API 2 秒内未就绪，请重启应用或联系作者");
      return;
    }
    var api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.init !== "function") {
      setTimeout(_bootstrap, 50);
      return;
    }
    api.init()
      .then(applyState)
      .catch(function () { setTimeout(_bootstrap, 100); });
    try { inputEl && inputEl.focus(); } catch (e) {}
  }
  window.addEventListener("pywebviewready", _bootstrap);
  _bootstrap();
})();
