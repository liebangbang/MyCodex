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
      .replace(/>/g, "&gt;");
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
  function appendUser(text) {
    finalizeAssistant();
    const m = document.createElement("div");
    m.className = "msg user";
    const b = document.createElement("div");
    b.className = "bubble";
    b.textContent = text;
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

  function appendTool(name, summary) {
    finalizeAssistant();
    const card = document.createElement("div");
    card.className = "tool-card";
    card.innerHTML =
      '<div class="tool-head"><span class="tool-dot"></span>' +
      "<span>" + esc(name) + '</span><span class="tool-summary">' + esc(summary) + "</span></div>" +
      '<div class="tool-result" style="display:none"></div>';
    messagesEl.appendChild(card);
    currentToolCard = card;
    scrollDown();
  }

  function appendToolResult(name, summary, result) {
    const card = currentToolCard || (function () { appendTool(name, summary); return currentToolCard; })();
    const r = card.querySelector(".tool-result");
    r.style.display = "block";
    r.textContent = result;
    scrollDown();
  }

  // ---------------- 产物 ----------------
  function renderArtifacts(list) {
    artListEl.innerHTML = "";
    artCountEl.textContent = list.length;
    list.forEach((a) => artListEl.appendChild(makeArtItem(a)));
  }

  function makeArtItem(a) {
    const item = document.createElement("div");
    item.className = "art-item";
    item.dataset.path = a.path;
    const ext = (a.name.split(".").pop() || "").toLowerCase();
    let cls = "";
    if (ext === "md" || ext === "txt") cls = "green";
    else if (ext === "json" || ext === "yml" || ext === "yaml") cls = "amber";
    const icon = document.createElement("div");
    icon.className = "art-icon " + cls;
    icon.innerHTML =
      '<svg class="icon" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>';
    const meta = document.createElement("div");
    meta.className = "art-meta";
    meta.innerHTML =
      '<div class="art-name">' + esc(a.name) + "</div>" +
      '<div class="art-sub">' + esc(a.size_str || (a.size + "B")) + "</div>";
    item.appendChild(icon);
    item.appendChild(meta);
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
      previewEl.innerHTML =
        '<div class="preview-head">' + esc(res.name) + "  ·  " + esc(String(res.size)) + "B</div>" +
        esc(res.content);
      previewEl.scrollTop = 0;
    });
  }

  // ---------------- 任务列表 ----------------
  function renderSessions(sessions) {
    taskListEl.innerHTML = "";
    sessions.forEach((s) => {
      const item = document.createElement("div");
      item.className = "task-item" + (s.name === activeTask ? " active" : "");
      item.dataset.name = s.name;
      const date = new Date((s.updated || 0) * 1000);
      const rel = isNaN(date) ? "" : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
      item.innerHTML =
        '<div class="task-name">' + esc(s.name) + "</div>" +
        '<div class="task-meta">' + (s.msg_count || 0) + " 条 · " + esc(rel) + "</div>";
      item.onclick = () => openTask(s.name);
      taskListEl.appendChild(item);
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
    if (entry.role === "user") appendUser(entry.text);
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
    setHeader(st);
    renderSessions(st.sessions || []);
    window._sessionsCache = st.sessions || [];
    renderArtifacts(st.artifacts || []);
    if (st.need_key) {
      appendSystem("未配置 API Key，请在 ~/.config/mycode/config.json 设置 api_key。");
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
    if (!text || busy || !apiReady()) return;
    inputEl.value = "";
    autoResize();
    setBusy(true);
    window.pywebview.api.send(text).then(() => {
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

  // ---------------- 图片输入 / OCR ----------------
  const fileImgEl = $("fileImg");
  const imgPreviewEl = $("imgPreview");
  const imgThumbEl = $("imgThumb");
  const imgNameEl = $("imgName");
  let pendingImageDataUrl = null;

  function showImgPreview(dataUrl, name) {
    pendingImageDataUrl = dataUrl;
    imgThumbEl.src = dataUrl;
    imgNameEl.textContent = name || "图片";
    imgPreviewEl.style.display = "flex";
    imgPreviewEl.scrollIntoView({ block: "nearest" });
  }

  function clearImgPreview() {
    pendingImageDataUrl = null;
    imgPreviewEl.style.display = "none";
    imgThumbEl.removeAttribute("src");
    fileImgEl.value = "";
  }

  function handleImageFile(file) {
    if (!file || !/^image\//.test(file.type || "")) {
      appendSystem("仅支持图片文件（PNG/JPG 等）");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => showImgPreview(reader.result, file.name);
    reader.onerror = () => appendSystem("读取图片失败");
    reader.readAsDataURL(file);
  }

  $("btnImg").onclick = () => fileImgEl.click();
  fileImgEl.onchange = () => {
    if (fileImgEl.files && fileImgEl.files[0]) handleImageFile(fileImgEl.files[0]);
    fileImgEl.value = "";
  };
  $("btnImgCancel").onclick = clearImgPreview;

  $("btnOcr").onclick = () => {
    if (!pendingImageDataUrl) return;
    if (!apiReady()) return;
    const btn = $("btnOcr");
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "识别中…";
    window.pywebview.api.ocr_image(pendingImageDataUrl).then((res) => {
      btn.disabled = false;
      btn.textContent = old;
      if (!res || res.error) {
        appendSystem("OCR 失败：" + (res && res.error ? res.error : "未知错误"));
        return;
      }
      const text = (res.text || "").trim();
      if (!text) {
        appendSystem("未在图片中识别到文字");
        return;
      }
      // 把识别结果插入输入框，用户可编辑后发送
      const prefix = inputEl.value.trim() ? "\n\n" : "";
      inputEl.value += prefix + "[图片OCR识别结果]\n" + text;
      autoResize();
      clearImgPreview();
      inputEl.focus();
    }).catch((e) => {
      btn.disabled = false;
      btn.textContent = old;
      appendSystem("OCR 调用失败：" + e);
    });
  };

  // 粘贴图片
  document.addEventListener("paste", (e) => {
    if (!e.clipboardData || !e.clipboardData.items) return;
    const items = e.clipboardData.items;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf("image/") === 0) {
        const file = items[i].getAsFile();
        if (file) {
          e.preventDefault();
          handleImageFile(file);
          return;
        }
      }
    }
  });

  // 拖拽图片到输入区
  const composer = document.querySelector(".composer");
  composer.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  composer.addEventListener("drop", (e) => {
    e.preventDefault();
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) {
      handleImageFile(files[0]);
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

  // ---------------- 启动 ----------------
  initTheme();
  window.addEventListener("pywebviewready", function () {
    window.pywebview.api.init().then(applyState);
    inputEl.focus();
  });
})();
