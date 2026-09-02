(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  let view = "active";
  let cfg = {};
  let allTasks = [];
  let lastDoneIds = new Set();
  let notifyReady = false;
  let pendingDeleteId = null;
  let pendingDeleteName = "";
  let resolveTimer = null;
  let dragDepth = 0;
  let refreshBusy = false;
  let lastRenderedOrder = [];
  const rowCache = new Map();
  let statusBarCache = "";
  let rpcCache = "";
  let lastRpc = null;

  const fmtSize = (n) => {
    if (!n) return "—";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
  };

  const fmtSpeed = (b) => (b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB/s` : b ? `${(b / 1024).toFixed(1)} KB/s` : "—");

  const fmtEta = (t) => {
    if (t.status !== "running" || !t.speed || t.downloaded >= t.size) return "—";
    const left = (t.size - t.downloaded) / t.speed;
    if (left < 60) return `${Math.ceil(left)} 秒`;
    if (left < 3600) return `${Math.ceil(left / 60)} 分钟`;
    return `${(left / 3600).toFixed(1)} 小时`;
  };

  const statusLabel = (s) => ({
    pending: "等待中", running: "下载中", paused: "已暂停",
    done: "已完成", error: "失败",
  }[s] || s);

  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

  function toast(msg, type = "info") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    $("#toastWrap").appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  async function api(method, path, body) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new Error("无法连接下载服务");
    }
    let data;
    try {
      data = await res.json();
    } catch {
      throw new Error(`服务响应异常 (${res.status})`);
    }
    if (data.code !== 0) throw new Error(data.msg || "请求失败");
    return data.data;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
  }

  function parseUrls(text) {
    return [...new Set(
      text.split(/\n+/).map((s) => s.trim().replace(/htype=&randtype=/g, "htype&randtype"))
        .filter((s) => /^https?:\/\//i.test(s))
    )];
  }

  function looksResolvable(url) {
    if (url.length < 20) return false;
    if (url.includes("baidupcs.com") || url.includes("antpcdn.com")) {
      return url.includes("size=") && url.includes("/file/");
    }
    return true;
  }

  function filterTasks(list) {
    let r = list;
    if (view === "active") r = r.filter((t) => ["pending", "running", "paused"].includes(t.status));
    else if (view === "done") r = r.filter((t) => t.status === "done");
    else if (view === "error") r = r.filter((t) => t.status === "error");

    const q = ($("#searchInput").value || "").trim().toLowerCase();
    if (q) r = r.filter((t) => t.name.toLowerCase().includes(q) || t.url.toLowerCase().includes(q));

    const sort = $("#sortSelect").value;
    r = [...r].sort((a, b) => {
      switch (sort) {
        case "time-asc": return a.createdAt - b.createdAt;
        case "name-asc": return a.name.localeCompare(b.name, "zh");
        case "size-desc": return b.size - a.size;
        case "progress-desc": return (b.progress || 0) - (a.progress || 0);
        default: return b.createdAt - a.createdAt;
      }
    });
    return r;
  }

  function updateRpcStatus(rpc) {
    if (!rpc) return;
    lastRpc = rpc;
    const key = [
      rpc.running,
      rpc.localUrl,
      rpc.lanUrl,
      rpc.remote,
      rpc.tokenEnabled,
    ].join("|");
    if (key === rpcCache) return;
    rpcCache = key;

    const dot = $("#rpcDot");
    dot.className = `rpc-dot ${rpc.running ? "online" : "offline"}`;

    let addr = rpc.localUrl || "—";
    if (rpc.running && rpc.remote && rpc.lanUrl) {
      addr = rpc.lanUrl;
    } else if (rpc.running) {
      addr = rpc.localUrl || addr;
    }

    const tokenHint = rpc.tokenEnabled ? " · 已启用令牌" : "";
    const remoteHint = rpc.remote ? " · 远程" : " · 仅本机";
    $("#rpcAddr").textContent = `${addr}${remoteHint}${tokenHint}`;
    $("#rpcAddr").title = rpc.running
      ? `本机: ${rpc.localUrl || "—"}${rpc.lanUrl ? `\n局域网: ${rpc.lanUrl}` : ""}`
      : "RPC 服务未运行";
  }

  function updateRpcHelp(rpc) {
    const port = Number($("#cfgRpcPort")?.value || cfg.rpcPort || 18766);
    const base = rpc?.running
      ? (rpc.localUrl || `http://127.0.0.1:${port}`)
      : `http://127.0.0.1:${port}`;
    const token = ($("#cfgRpcToken")?.value || cfg.rpcToken || "").trim();
    const authLine = token ? `\n  -H "Authorization: Bearer ${token}" \\` : "";
    const running = rpc?.running === true;

    const dot = $("#rpcHelpDot");
    if (dot) dot.className = `rpc-dot ${running ? "online" : "offline"}`;

    const statusText = $("#rpcHelpStatusText");
    if (statusText) {
      statusText.textContent = running
        ? (rpc.remote ? "RPC 服务运行中 · 已开启远程访问" : "RPC 服务运行中 · 仅本机访问")
        : "RPC 服务未运行";
    }

    const localEl = $("#rpcHelpLocal");
    if (localEl) localEl.textContent = running ? (rpc.localUrl || base) : `${base}（未运行）`;

    const lanRow = $("#rpcHelpLanRow");
    const lanEl = $("#rpcHelpLan");
    if (lanRow && lanEl) {
      const showLan = running && rpc.remote && rpc.lanUrl;
      lanRow.classList.toggle("hidden", !showLan);
      if (showLan) lanEl.textContent = rpc.lanUrl;
    }

    const tokenTag = $("#rpcHelpTokenTag");
    if (tokenTag) {
      tokenTag.textContent = token ? "已启用令牌" : "未启用令牌";
      tokenTag.classList.toggle("on", !!token);
    }

    const authEl = $("#rpcHelpAuth");
    if (authEl) {
      authEl.textContent = token
        ? `Authorization: Bearer ${token}\nX-ArkDownloader-Token: ${token}`
        : "Authorization: Bearer <你的令牌>\nX-ArkDownloader-Token: <你的令牌>";
    }

    const setPre = (sel, text) => {
      const el = $(sel);
      if (el) el.textContent = text;
    };

    setPre("#rpcHelpAdd", `curl -X POST ${base}/api/v1/tasks \\
  -H "Content-Type: application/json" \\${authLine}
  -d "{\\"url\\":\\"https://...\\",\\"dir\\":\\"C:/Users/你/Downloads\\"}"`);

    setPre("#rpcHelpBatch", `curl -X POST ${base}/api/v1/tasks/batch \\
  -H "Content-Type: application/json" \\${authLine}
  -d "{\\"reqs\\":[{\\"req\\":{\\"url\\":\\"https://...\\"}}]}"`);

    setPre("#rpcHelpList", token
      ? `curl ${base}/api/v1/tasks \\\n  -H "Authorization: Bearer ${token}"`
      : `curl ${base}/api/v1/tasks`);

    const baseBtn = $("#btnCopyRpcBase");
    if (baseBtn) baseBtn.dataset.copyText = running ? (rpc.localUrl || base) : base;
  }

  async function copyFromHelp(targetId) {
    const el = document.getElementById(targetId);
    if (!el) return;
    const text = el.dataset?.copyText || el.textContent || "";
    if (!text) return;
    await copyText(text.trim());
    toast("已复制", "success");
  }

  function openRpcHelpModal() {
    updateRpcHelp(lastRpc);
    $("#rpcHelpModal").classList.remove("hidden");
  }

  function closeRpcHelpModal() {
    $("#rpcHelpModal").classList.add("hidden");
  }

  function updateStatusBar(list) {
    const running = list.filter((t) => t.status === "running");
    const pending = list.filter((t) => t.status === "pending");
    const speed = running.reduce((s, t) => s + (t.speed || 0), 0);
    const next = [
      `speed:${fmtSpeed(speed)}`,
      `run:${running.length}`,
      `pend:${pending.length}`,
    ].join("|");
    if (next === statusBarCache) return;
    statusBarCache = next;
    $("#statSpeed").innerHTML = `总速度<strong>${fmtSpeed(speed)}</strong>`;
    $("#statRunning").innerHTML = `进行中<strong>${running.length}</strong>`;
    $("#statPending").innerHTML = `排队<strong>${pending.length}</strong>`;
  }

  function actionsKey(t) {
    return `${t.status}|${t.error || ""}`;
  }

  function rowDataKey(t) {
    return [
      t.size,
      t.downloaded,
      t.progress || 0,
      t.speed || 0,
      t.status,
      fmtEta(t),
      t.error || "",
    ].join("|");
  }

  function buildActions(t) {
    const actions = [];
    if (t.status === "running") actions.push(`<button class="btn sm" data-act="pause" data-id="${t.id}">暂停</button>`);
    if (["paused", "error", "pending"].includes(t.status)) {
      actions.push(`<button class="btn sm primary" data-act="resume" data-id="${t.id}">${t.status === "error" ? "重试" : "继续"}</button>`);
    }
    if (t.status === "done") {
      actions.push(`<button class="btn sm" data-act="open" data-id="${t.id}">打开目录</button>`);
    }
    actions.push(`<button class="btn sm icon-only" data-act="copy" data-id="${t.id}" title="复制链接">⎘</button>`);
    actions.push(`<button class="btn sm danger" data-act="del" data-id="${t.id}">删除</button>`);
    return actions.join("");
  }

  function renderRow(t) {
    const pct = t.progress || 0;
    const barCls = t.status === "done" ? "done" : t.status === "error" ? "error" : "";
    const errHtml = t.status === "error" && t.error
      ? `<div class="error-tip" title="${esc(t.error)}">${esc(t.error)}</div>` : "";

    return `<tr data-id="${t.id}">
      <td>
        <div class="file-name" title="${esc(t.name)}">${esc(t.name)}</div>
        <div class="file-sub">${t.kind === "baidu" ? "百度直链" : "HTTP"}</div>
        ${errHtml}
      </td>
      <td class="cell-size">${fmtSize(t.size)}</td>
      <td class="cell-progress">
        <div class="progress-bar ${barCls}"><i style="width:${Math.min(100, pct)}%"></i></div>
        <div class="progress-text">${fmtSize(t.downloaded)} / ${fmtSize(t.size)} (${pct}%)</div>
      </td>
      <td class="cell-eta">${fmtEta(t)}</td>
      <td class="cell-speed">${fmtSpeed(t.speed)}</td>
      <td class="cell-status"><span class="status ${t.status}">${statusLabel(t.status)}</span></td>
      <td class="cell-actions"><div class="actions">${buildActions(t)}</div></td>
    </tr>`;
  }

  function patchRow(tr, t) {
    const dataKey = rowDataKey(t);
    const actKey = actionsKey(t);
    const prev = rowCache.get(t.id) || {};
    if (prev.data === dataKey && prev.act === actKey) return;
    rowCache.set(t.id, { data: dataKey, act: actKey });

    const pct = t.progress || 0;
    const barCls = t.status === "done" ? "done" : t.status === "error" ? "error" : "";

    if (prev.data !== dataKey) {
      tr.querySelector(".cell-size").textContent = fmtSize(t.size);

      const bar = tr.querySelector(".progress-bar");
      const nextBarCls = `progress-bar ${barCls}`.trim();
      if (bar.className !== nextBarCls) bar.className = nextBarCls;
      const barFill = bar.querySelector("i");
      const w = `${Math.min(100, pct)}%`;
      if (barFill.style.width !== w) barFill.style.width = w;

      tr.querySelector(".progress-text").textContent =
        `${fmtSize(t.downloaded)} / ${fmtSize(t.size)} (${pct}%)`;
      tr.querySelector(".cell-eta").textContent = fmtEta(t);
      tr.querySelector(".cell-speed").textContent = fmtSpeed(t.speed);

      const st = tr.querySelector(".cell-status .status");
      const nextStCls = `status ${t.status}`;
      if (st.className !== nextStCls) st.className = nextStCls;
      const stLabel = statusLabel(t.status);
      if (st.textContent !== stLabel) st.textContent = stLabel;

      const errOld = tr.querySelector(".error-tip");
      if (t.status === "error" && t.error) {
        if (!errOld) {
          const td = tr.querySelector("td");
          const d = document.createElement("div");
          d.className = "error-tip";
          d.title = t.error;
          d.textContent = t.error;
          td.appendChild(d);
        } else if (errOld.textContent !== t.error) {
          errOld.textContent = t.error;
          errOld.title = t.error;
        }
      } else if (errOld) {
        errOld.remove();
      }
    }

    if (prev.act !== actKey) {
      tr.querySelector(".cell-actions .actions").innerHTML = buildActions(t);
    }
  }

  function orderChanged(shown) {
    if (shown.length !== lastRenderedOrder.length) return true;
    return shown.some((t, i) => t.id !== lastRenderedOrder[i]);
  }

  function reorderRows(tbody, shown) {
    if (!orderChanged(shown)) return;
    const frag = document.createDocumentFragment();
    for (const t of shown) {
      const row = tbody.querySelector(`tr[data-id="${t.id}"]`);
      if (row) frag.appendChild(row);
    }
    tbody.appendChild(frag);
    lastRenderedOrder = shown.map((t) => t.id);
  }

  function renderTasks(shown) {
    const tbody = $("#taskBody");
    const existing = new Map([...tbody.querySelectorAll("tr")].map((r) => [r.dataset.id, r]));
    const nextIds = new Set(shown.map((t) => t.id));

    for (const t of shown) {
      if (existing.has(t.id)) {
        patchRow(existing.get(t.id), t);
      } else {
        tbody.insertAdjacentHTML("beforeend", renderRow(t));
        const row = tbody.querySelector(`tr[data-id="${t.id}"]`);
        rowCache.set(t.id, { data: rowDataKey(t), act: actionsKey(t) });
        row?.classList.add("row-highlight");
        row?.addEventListener("animationend", () => row.classList.remove("row-highlight"), { once: true });
        existing.set(t.id, row);
      }
    }

    for (const [id, row] of existing) {
      if (!nextIds.has(id)) {
        row.remove();
        rowCache.delete(id);
      }
    }

    reorderRows(tbody, shown);

    const empty = shown.length === 0;
    const emptyEl = $("#emptyState");
    if (emptyEl.classList.contains("hidden") === empty) {
      emptyEl.classList.toggle("hidden", !empty);
    }
  }

  function checkNotifications(list) {
    if (!notifyReady || cfg.notifyOnComplete === false) return;
    for (const t of list) {
      if (t.status === "done" && !lastDoneIds.has(t.id)) {
        toast(`下载完成：${t.name}`, "success");
      }
    }
    lastDoneIds = new Set(list.filter((t) => t.status === "done").map((t) => t.id));
  }

  async function refresh() {
    if (refreshBusy) return;
    refreshBusy = true;
    try {
      const [list, info] = await Promise.all([
        api("GET", "/api/v1/tasks"),
        api("GET", "/api/v1/info"),
      ]);
      checkNotifications(list);
      allTasks = list;
      updateRpcStatus(info.rpc);

      $("#badgeActive").textContent = list.filter((t) => ["pending", "running", "paused"].includes(t.status)).length;
      $("#badgeDone").textContent = list.filter((t) => t.status === "done").length;
      $("#badgeError").textContent = list.filter((t) => t.status === "error").length;
      updateStatusBar(list);

      if (view !== "settings") {
        renderTasks(filterTasks(list));
      }
    } catch (e) {
      console.error(e);
    } finally {
      refreshBusy = false;
    }
  }

  async function loadCfg() {
    cfg = await api("GET", "/api/v1/config");
    $("#cfgDir").value = cfg.downloadDir || "";
    $("#cfgConn").value = cfg.connections || 8;
    $("#cfgChunkMb").value = cfg.chunkSizeMb || 1;
    $("#cfgMaxRun").value = cfg.maxRunning || 3;
    $("#cfgAutoStart").checked = cfg.autoStart !== false;
    $("#cfgNotify").checked = cfg.notifyOnComplete !== false;
    $("#cfgUserAgent").value = cfg.userAgent || "";
    $("#cfgHttpUserAgent").value = cfg.httpUserAgent || "ArkDownloader/1.0";
    $("#cfgRpcRemote").checked = cfg.rpcRemote === true;
    $("#cfgRpcPort").value = cfg.rpcPort ?? 18766;
    $("#cfgRpcToken").value = cfg.rpcToken || "";
    $("#addDir").value = cfg.downloadDir || "";
    $("#addConn").value = cfg.connections || 8;
  }

  function setView(v) {
    view = v;
    $$(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.view === v));
    $("#viewTasks").classList.toggle("hidden", v === "settings");
    $("#viewSettings").classList.toggle("hidden", v !== "settings");
    if (v !== "settings") {
      lastRenderedOrder = [];
      refresh();
    }
  }

  function resetAddForm() {
    $("#addUrl").value = "";
    $("#addName").value = "";
    $("#resolveInfo").classList.add("hidden");
    $("#resolveInfo").textContent = "";
  }

  function openAddModal(prefill = "") {
    resetAddForm();
    $("#addModal").classList.remove("hidden");
    if (prefill) $("#addUrl").value = prefill;
    $("#addUrl").focus();
    if (prefill) scheduleResolve();
  }

  function closeAddModal() {
    $("#addModal").classList.add("hidden");
    clearTimeout(resolveTimer);
  }

  async function resolveUrls(showToast = false) {
    const urls = parseUrls($("#addUrl").value);
    const box = $("#resolveInfo");
    if (!urls.length) {
      if (showToast) toast("请输入有效的 http(s) 链接", "error");
      return;
    }
    box.className = "resolve-info info";
    box.textContent = "正在检测…";
    box.classList.remove("hidden");
    const lines = [];
    let ok = 0;
    for (const url of urls.slice(0, 5)) {
      try {
        const r = await api("POST", "/api/v1/resolve", { url });
        lines.push(`✓ ${r.name} · ${fmtSize(r.size)} · ${r.kind === "baidu" ? "百度" : "HTTP"}`);
        ok++;
        if (urls.length === 1 && !$("#addName").value) $("#addName").value = r.name;
      } catch (e) {
        lines.push(`✗ ${url.slice(0, 40)}… → ${e.message}`);
      }
    }
    if (urls.length > 5) lines.push(`… 还有 ${urls.length - 5} 条未检测`);
    box.className = ok ? "resolve-info ok" : "resolve-info err";
    box.innerHTML = lines.join("<br>");
    if (showToast && ok) toast(`已识别 ${ok} 个链接`, "success");
  }

  function scheduleResolve() {
    clearTimeout(resolveTimer);
    resolveTimer = setTimeout(() => {
      const urls = parseUrls($("#addUrl").value);
      if (urls.length === 1 && looksResolvable(urls[0])) resolveUrls();
      else if (urls.length > 1) resolveUrls();
    }, 800);
  }

  async function submitAdd() {
    const urls = parseUrls($("#addUrl").value);
    if (!urls.length) { toast("请输入下载链接", "error"); return; }

    const dir = $("#addDir").value.trim();
    const conn = Number($("#addConn").value) || 8;
    const singleName = $("#addName").value.trim();
    const btn = $("#btnSubmitAdd");
    btn.disabled = true;

    try {
      let added = 0;
      if (urls.length === 1) {
        await api("POST", "/api/v1/tasks", { url: urls[0], dir, name: singleName, connections: conn });
        added = 1;
      } else {
        const result = await api("POST", "/api/v1/tasks/batch", {
          reqs: urls.map((url) => ({
            req: { url },
            opts: { path: dir, extra: { connections: conn } },
          })),
        });
        const ids = Array.isArray(result) ? result : (result.ids || []);
        added = ids.length;
        const errs = result.errors || [];
        if (errs.length) toast(`${errs.length} 条链接添加失败`, "error");
      }
      toast(`已添加 ${added} 个任务`, "success");
      closeAddModal();
      setView("active");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
    }
  }

  function confirmDelete(id, name) {
    pendingDeleteId = id;
    pendingDeleteName = name;
    $("#delText").textContent = `确定删除「${name}」？`;
    $("#delFile").checked = false;
    $("#delModal").classList.remove("hidden");
  }

  function getTaskById(id) {
    return allTasks.find((t) => t.id === id);
  }

  async function init() {
    try {
      const info = await api("GET", "/api/v1/info");
      $("#appVersion").textContent = `v${info.version}`;
      updateRpcStatus(info.rpc);
    } catch (_) {}
    await loadCfg();
    await refresh();
    lastDoneIds = new Set(allTasks.filter((t) => t.status === "done").map((t) => t.id));
    notifyReady = true;
    setInterval(refresh, 1000);
  }

  // Nav & toolbar
  $$(".nav-item").forEach((el) => el.addEventListener("click", () => setView(el.dataset.view)));
  $("#btnAdd").onclick = () => openAddModal();
  $("#btnCloseModal").onclick = closeAddModal;
  $("#btnPauseAll").onclick = () => api("PUT", "/api/v1/tasks/pause").then(() => { toast("已暂停全部"); refresh(); });
  $("#btnResumeAll").onclick = () => api("PUT", "/api/v1/tasks/continue").then(() => { toast("已继续全部"); refresh(); });
  $("#btnClearDone").onclick = async () => {
    await api("POST", "/api/v1/tasks/clear-completed");
    toast("已清空完成任务");
    refresh();
  };
  $("#searchInput").oninput = () => {
    if (view !== "settings") {
      lastRenderedOrder = [];
      renderTasks(filterTasks(allTasks));
    }
  };
  $("#sortSelect").onchange = () => {
    if (view !== "settings") {
      lastRenderedOrder = [];
      renderTasks(filterTasks(allTasks));
    }
  };

  // Add modal
  $("#addUrl").oninput = scheduleResolve;
  $("#btnResolve").onclick = () => resolveUrls(true);
  $("#btnSubmitAdd").onclick = submitAdd;
  $("#addModal").addEventListener("click", (e) => {
    if (e.target === $("#addModal")) closeAddModal();
  });

  // Delete modal
  $("#btnDelCancel").onclick = () => {
    pendingDeleteId = null;
    $("#delModal").classList.add("hidden");
  };
  $("#delModal").addEventListener("click", (e) => {
    if (e.target === $("#delModal")) {
      pendingDeleteId = null;
      $("#delModal").classList.add("hidden");
    }
  });
  $("#btnDelOk").onclick = async () => {
    if (!pendingDeleteId) return;
    const delFile = $("#delFile").checked;
    const q = delFile ? "?file=true" : "";
    try {
      await api("DELETE", `/api/v1/tasks/${pendingDeleteId}${q}`);
      toast("任务已删除", "success");
      pendingDeleteId = null;
      $("#delModal").classList.add("hidden");
      refresh();
    } catch (e) {
      toast(e.message, "error");
    }
  };

  // Task actions (event delegation)
  $("#taskBody").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    e.preventDefault();
    const act = btn.dataset.act;
    const id = btn.dataset.id;
    const task = getTaskById(id);
    try {
      if (act === "pause") {
        await api("PUT", `/api/v1/tasks/${id}/pause`);
        await refresh();
        return;
      }
      if (act === "resume") {
        await api("PUT", `/api/v1/tasks/${id}/continue`);
        await refresh();
        return;
      }
      if (act === "open" && task) {
        await api("POST", "/api/v1/open-folder", { path: task.out });
        return;
      }
      if (act === "copy" && task) {
        await copyText(task.url);
        toast("链接已复制", "success");
        return;
      }
      if (act === "del" && task) {
        confirmDelete(id, task.name);
        return;
      }
      await refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  function openContactModal() {
    $("#contactModal").classList.remove("hidden");
  }
  function closeContactModal() {
    $("#contactModal").classList.add("hidden");
  }

  $("#btnContact").onclick = openContactModal;
  $("#btnCloseContact").onclick = closeContactModal;
  $("#btnContactOk").onclick = closeContactModal;
  $("#btnCopyContactPwd").onclick = () => copyText("arkDownloader").then(() => toast("密码已复制", "success"));
  $("#contactModal").addEventListener("click", (e) => {
    if (e.target === $("#contactModal")) closeContactModal();
  });

  // Settings
  $("#btnRpcHelp").onclick = openRpcHelpModal;
  $("#btnCloseRpcHelp").onclick = closeRpcHelpModal;
  $("#btnRpcHelpOk").onclick = closeRpcHelpModal;
  $("#btnCopyRpcBase").onclick = () => copyFromHelp("btnCopyRpcBase");
  $("#rpcHelpModal").addEventListener("click", (e) => {
    if (e.target === $("#rpcHelpModal")) closeRpcHelpModal();
    const copyBtn = e.target.closest("[data-copy-target]");
    if (copyBtn) copyFromHelp(copyBtn.dataset.copyTarget);
  });
  $("#cfgRpcPort").addEventListener("input", () => {
    if (!$("#rpcHelpModal").classList.contains("hidden")) updateRpcHelp(lastRpc);
  });
  $("#cfgRpcToken").addEventListener("input", () => {
    if (!$("#rpcHelpModal").classList.contains("hidden")) updateRpcHelp(lastRpc);
  });
  $("#settingsForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const saved = await api("PUT", "/api/v1/config", {
        downloadDir: $("#cfgDir").value.trim(),
        connections: Number($("#cfgConn").value),
        chunkSizeMb: Number($("#cfgChunkMb").value),
        maxRunning: Number($("#cfgMaxRun").value),
        autoStart: $("#cfgAutoStart").checked,
        notifyOnComplete: $("#cfgNotify").checked,
        userAgent: $("#cfgUserAgent").value.trim(),
        httpUserAgent: $("#cfgHttpUserAgent").value.trim(),
        rpcRemote: $("#cfgRpcRemote").checked,
        rpcPort: Number($("#cfgRpcPort").value),
        rpcToken: $("#cfgRpcToken").value.trim(),
      });
      await loadCfg();
      if (saved.rpcStatus) {
        rpcCache = "";
        updateRpcStatus(saved.rpcStatus);
      }
      if (saved.needsAppRestart) {
        toast("RPC 端口已变更，请重启应用使界面生效", "error");
      } else if (saved.rpcRestarted) {
        toast("RPC 服务已重新加载", "success");
      } else {
        toast("设置已保存", "success");
      }
    } catch (e) {
      toast(e.message, "error");
    }
  };

  // Drag & drop (fix flicker with depth counter)
  const dropZone = $("#dropZone");
  const overlay = $("#dropOverlay");
  dropZone.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragDepth++;
    overlay.classList.remove("hidden");
  });
  dropZone.addEventListener("dragover", (e) => e.preventDefault());
  dropZone.addEventListener("dragleave", (e) => {
    e.preventDefault();
    dragDepth--;
    if (dragDepth <= 0) {
      dragDepth = 0;
      overlay.classList.add("hidden");
    }
  });
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    overlay.classList.add("hidden");
    const text = e.dataTransfer.getData("text") || e.dataTransfer.getData("text/plain");
    if (text && /https?:\/\//.test(text)) openAddModal(text.trim());
  });

  // Keyboard
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "n") { e.preventDefault(); openAddModal(); }
    if (e.key === "Escape") {
      closeAddModal();
      closeRpcHelpModal();
      pendingDeleteId = null;
      $("#delModal").classList.add("hidden");
    }
  });

  init();
})();
