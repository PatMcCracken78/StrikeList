// ==UserScript==
// @name         Strike List Mobile
// @namespace    https://github.com/PatMcCracken78/StrikeList
// @version      1.0.0
// @description  Your Torn target list in a slide-out panel: live status timers, FF / FF now / FR, one-tap attack, FF Scouter estimates, share codes with the Strike List desktop app. Works in Torn PDA and desktop browsers.
// @author       _Storm_ [2678385]
// @license      GPL-3.0-or-later
// @match        https://www.torn.com/*
// @icon         https://raw.githubusercontent.com/PatMcCracken78/StrikeList/main/assets/logo.png
// @grant        GM_xmlhttpRequest
// @connect      ffscouter.com
// @connect      api.torn.com
// @run-at       document-end
// @downloadURL  https://raw.githubusercontent.com/PatMcCracken78/StrikeList/main/mobile/strike-list.user.js
// @updateURL    https://raw.githubusercontent.com/PatMcCracken78/StrikeList/main/mobile/strike-list.user.js
// ==/UserScript==

/*
 * Strike List Mobile - Copyright (C) 2026 _Storm_ [2678385]
 * Free software under the GNU General Public License v3 or later.
 *
 * Torn API use: read-only. Your key and your list are stored only on this device
 * (this browser's storage for torn.com). Nothing is shared, except - only if you turn
 * FF Scouter on - your key and the player IDs you look up are sent to ffscouter.com.
 * Key access level: Limited.
 */
(function () {
  "use strict";
  if (window.__strikeListMobile) return;          // never run twice on one page
  window.__strikeListMobile = true;

  const VERSION = "1.0.0";
  const PDA_KEY = "###PDA-APIKEY###";             // Torn PDA replaces this with your key
  const HEADER_IMG = "data:image/png;base64,__HEADER__";
  const ICON_IMG = "data:image/png;base64,__ICON__";
  const API = "https://api.torn.com";
  const FFS = "https://ffscouter.com";
  const ATTACK_URL = (id) => `https://www.torn.com/page.php?sid=attack&user2ID=${id}`;
  const PROFILE_URL = (id) => `https://www.torn.com/profiles.php?XID=${id}`;
  const WINS = ["Attacked", "Mugged", "Hospitalized", "Arrested", "Special", "Bounty"];
  const FLAG_COLORS = [null, "#5fd38a", "#e8c547", "#ff6b6b"];

  // ------------------------------------------------------------------ storage
  const K = (k) => "strikelist_" + k;
  const load = (k, d) => { try { const v = localStorage.getItem(K(k)); return v ? JSON.parse(v) : d; } catch (e) { return d; } };
  const save = (k, v) => { try { localStorage.setItem(K(k), JSON.stringify(v)); } catch (e) { /* storage full / blocked */ } };

  const DEFAULTS = { key: "", ffs: false, minFF: 2.5, side: "right", top: 45, filter: "ok", sort: "fr", keepFlagged: true };
  let cfg = Object.assign({}, DEFAULTS, load("cfg", {}));
  let targets = load("targets", {});          // id -> {name, level, status, life, last_action, updated, color, note, added}
  let hist = load("hist", { since: 0, wins: {} }); // wins: defenderId -> {ff, fr, ended, name, level}
  let ffs = load("ffs", {});                  // id -> {ff, bs, bsh, fetched}
  const saveCfg = () => save("cfg", cfg);
  const saveTargets = () => save("targets", targets);

  const pdaKeyValid = /^[A-Za-z0-9]{16}$/.test(PDA_KEY);
  const apiKey = () => (pdaKeyValid ? PDA_KEY : (cfg.key || "").trim());
  const isPDA = typeof window.PDA_httpGet === "function" || pdaKeyValid;

  // ------------------------------------------------------------------ helpers
  const now = () => Math.floor(Date.now() / 1000);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const hms = (s) => {
    s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
  };
  const ago = (ts) => {
    if (!ts) return ""; const s = Math.max(0, now() - ts);
    if (s < 60) return s + "s ago"; if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago";
  };
  const fmtFF = (v) => (v == null ? "–" : v > 3 ? "3.00+" : Number(v).toFixed(2));
  const parseIds = (text) => [...new Set((String(text).match(/(?<!\d)\d{1,8}(?!\d)/g) || []).map(Number))];

  // ------------------------------------------------------------------ share codes (same format as the desktop app)
  // "SL1:" + base64url(JSON {v:1, t:[[id, color, note], ...]})
  function b64urlEncode(str) {
    const bytes = new TextEncoder().encode(str); let bin = "";
    bytes.forEach((b) => (bin += String.fromCharCode(b)));
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function b64urlDecode(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "=";
    const bin = atob(s); const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  }
  function exportCode() {
    const t = Object.entries(targets).map(([id, x]) => {
      const row = [Number(id)];
      if (x.color || x.note) row.push(x.color || 0);
      if (x.note) row.push(x.note);
      return row;
    });
    return "SL1:" + b64urlEncode(JSON.stringify({ v: 1, t }));
  }
  function importCode(text) {
    text = String(text || "").trim();
    let rows = [];
    const m = text.match(/SL1:([A-Za-z0-9_-]+)/);
    if (m) {
      const data = JSON.parse(b64urlDecode(m[1]));
      rows = (data.t || []).map((r) => (Array.isArray(r) ? r : [r]));
    } else {
      rows = parseIds(text).map((id) => [id]);  // plain IDs / profile links also work
    }
    let added = 0, updated = 0;
    for (const [id, color, note] of rows) {
      if (!id) continue;
      const cur = targets[id];
      if (!cur) { targets[id] = { added: now(), color: color || 0, note: note || "" }; added++; }
      else { if (color) cur.color = color; if (note && !cur.note) cur.note = note; updated++; }
    }
    saveTargets();
    return { added, updated };
  }

  // ------------------------------------------------------------------ networking
  let lastCall = 0;
  const queue = [];
  let pumping = false;
  function torn(path, params = {}) {   // all Torn calls are queued >= 1.6 s apart (<= ~37/min)
    return new Promise((resolve, reject) => { queue.push({ path, params, resolve, reject }); pump(); });
  }
  async function pump() {
    if (pumping) return; pumping = true;
    while (queue.length) {
      const job = queue.shift();
      const wait = Math.max(0, lastCall + 1600 - Date.now());
      if (wait) await new Promise((r) => setTimeout(r, wait));
      lastCall = Date.now();
      try {
        const key = apiKey();
        if (!key) throw new Error("No API key - open ⚙ Settings");
        const q = new URLSearchParams({ ...job.params, key, comment: "StrikeListMobile" });
        const res = await fetch(`${API}/${job.path}?${q}`);
        const data = await res.json();
        if (data.error) throw new Error(`Torn API error ${data.error.code}: ${data.error.error}`);
        job.resolve(data);
      } catch (e) { job.reject(e); }
    }
    pumping = false;
  }
  function crossGet(url) {  // FF Scouter is another site: use PDA's / the script manager's request helper
    return new Promise((resolve, reject) => {
      if (typeof window.PDA_httpGet === "function") {
        window.PDA_httpGet(url, {}).then((r) => { try { resolve(JSON.parse(r.responseText)); } catch (e) { reject(e); } }).catch(reject);
      } else if (typeof GM_xmlhttpRequest === "function") {
        GM_xmlhttpRequest({ method: "GET", url, onload: (r) => { try { resolve(JSON.parse(r.responseText)); } catch (e) { reject(e); } }, onerror: () => reject(new Error("FF Scouter unreachable")) });
      } else {
        fetch(url).then((r) => r.json()).then(resolve, reject);
      }
    });
  }
  function crossPost(url, body) {
    return new Promise((resolve, reject) => {
      const data = JSON.stringify(body), headers = { "Content-Type": "application/json" };
      if (typeof window.PDA_httpPost === "function") {
        window.PDA_httpPost(url, headers, data).then((r) => { try { resolve(JSON.parse(r.responseText)); } catch (e) { reject(e); } }).catch(reject);
      } else if (typeof GM_xmlhttpRequest === "function") {
        GM_xmlhttpRequest({ method: "POST", url, headers, data, onload: (r) => { try { resolve(JSON.parse(r.responseText)); } catch (e) { reject(e); } }, onerror: () => reject(new Error("FF Scouter unreachable")) });
      } else {
        fetch(url, { method: "POST", headers, body: data }).then((r) => r.json()).then(resolve, reject);
      }
    });
  }

  // ------------------------------------------------------------------ data refresh
  function mod(m, k) { const v = Number((m || {})[k]); return v ? v : 1; }
  async function syncAttacks() {        // reads your recent attacks to know FF / FR of your last win on each target
    const first = !hist.since;
    let to = null, pages = 0, newest = hist.since;
    while (pages < (first ? 5 : 3)) {
      const params = { limit: 100, sort: "DESC", timestamp: now() };
      if (to) params.to = to;
      const data = await torn("v2/user/attacks", params);
      const atks = data.attacks || []; pages++;
      if (!atks.length) break;
      for (const a of atks) {
        newest = Math.max(newest, a.started || 0);
        const d = a.defender || {};
        if (!d.id || !a.attacker || !WINS.includes(a.result) || !(a.respect_gain > 0)) continue;
        const m = a.modifiers || {};
        const bonus = mod(m, "chain") * mod(m, "war") * mod(m, "retaliation") * mod(m, "group") * mod(m, "overseas") * mod(m, "warlord");
        const prev = hist.wins[d.id];
        if (!prev || prev.ended < a.ended) {
          hist.wins[d.id] = { ff: mod(m, "fair_fight"), fr: +(a.respect_gain / bonus).toFixed(2), ended: a.ended, name: d.name, level: d.level };
        }
      }
      const oldest = Math.min(...atks.map((a) => a.started || 0));
      if (atks.length < 100 || (hist.since && oldest <= hist.since)) break;
      to = oldest;
    }
    hist.since = Math.max(0, newest - 600);
    save("hist", hist);
  }
  async function refreshTarget(id) {
    const p = await torn(`user/${id}`, { selections: "profile" });
    const t = targets[id] || (targets[id] = { added: now(), color: 0, note: "" });
    Object.assign(t, {
      name: p.name, level: p.level, faction: (p.faction || {}).faction_name || "",
      status: { state: (p.status || {}).state, desc: (p.status || {}).description, until: (p.status || {}).until || 0 },
      life: p.life ? [p.life.current, p.life.maximum] : null,
      last_action: (p.last_action || {}).timestamp || 0, online: (p.last_action || {}).status || "",
      updated: now(), error: null,
    });
  }
  async function refreshFFS(ids, force) {
    if (!cfg.ffs || !ids.length) return;
    const fresh = (id) => ffs[id] && now() - ffs[id].fetched < 6 * 3600;
    const todo = ids.filter((id) => force || !fresh(id));
    for (let i = 0; i < todo.length; i += 205) {
      const chunk = todo.slice(i, i + 205);
      const data = await crossGet(`${FFS}/api/v1/get-stats?key=${apiKey()}&targets=${chunk.join(",")}`);
      if (data && data.error) throw new Error(`FF Scouter: ${data.error}`);
      for (const p of Array.isArray(data) ? data : []) {
        ffs[p.player_id] = { ff: p.fair_fight, bs: p.bs_estimate, bsh: p.bs_estimate_human, fetched: now() };
      }
    }
    save("ffs", ffs);
  }

  let busy = false, lastRefresh = 0, lastError = "", lastInfo = "", setMsg = "";
  async function refreshAll(force) {
    if (busy || !apiKey()) { if (!apiKey()) lastError = "No API key - open ⚙ Settings"; render(); return; }
    busy = true; lastError = ""; lastInfo = ""; render();
    const ids = Object.keys(targets).map(Number);
    try {
      await syncAttacks().catch((e) => { lastError = e.message; });
      // oldest first, so a long list refreshes fairly
      ids.sort((a, b) => (targets[a].updated || 0) - (targets[b].updated || 0));
      let n = 0;
      for (const id of ids) {
        if (!force && targets[id].updated && now() - targets[id].updated < 60) continue;
        try { await refreshTarget(id); } catch (e) { targets[id].error = e.message; lastError = e.message; if (/error (2|10|13|18):/.test(e.message)) break; }
        if (++n % 5 === 0) { saveTargets(); render(); }
      }
      saveTargets();
      await refreshFFS(ids, false).catch((e) => { lastError = e.message; });
    } finally { busy = false; lastRefresh = now(); render(); }
  }

  // ------------------------------------------------------------------ derived values
  function statusOf(t) {
    const s = t.status || {}; const left = (s.until || 0) - now();
    if (t.error && !s.state) return { text: "error", col: "#ff6b6b", ok: false };
    if (!s.state) return { text: "not loaded yet", col: "#8a929c", ok: false };
    if (s.state === "Hospital" || s.state === "Jail") {
      if (left > 0) return { text: `${s.state[0]} for ${hms(left)}`, col: "#ff6b6b", ok: false, left };
      return { text: `Out ${hms(-left)} ago*`, col: "#e8c547", ok: true };
    }
    if (s.state === "Traveling" || s.state === "Abroad") return { text: s.desc || s.state, col: "#6aa9ff", ok: false };
    if (s.state === "Federal") return { text: "Federal", col: "#ff6b6b", ok: false };
    if (s.state === "Okay") return { text: "Okay", col: "#5fd38a", ok: true };
    return { text: s.desc || s.state, col: "#8a929c", ok: false };
  }
  function row(id) {
    const t = targets[id], w = hist.wins[id] || {}, f = ffs[id] || {};
    return { id: Number(id), t, name: t.name || w.name || `#${id}`, level: t.level || w.level, ff: w.ff, fr: w.fr,
             ffNow: f.ff, bsh: f.bsh, st: statusOf(t), lastWin: w.ended };
  }
  const effFF = (r) => (r.ffNow != null ? r.ffNow : r.ff);

  // ------------------------------------------------------------------ UI
  const host = document.createElement("div");
  host.id = "strike-list-mobile";
  host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483000;";
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
    *{box-sizing:border-box;font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
    .tab{position:fixed;width:46px;height:62px;background:#15171a;border:1px solid #3a4047;display:flex;align-items:center;justify-content:center;
         box-shadow:0 2px 12px #000a;pointer-events:auto;touch-action:none;user-select:none;-webkit-user-select:none;cursor:grab;transition:left .18s,right .18s}
    .tab.right{right:0;border-right:none;border-radius:14px 0 0 14px}.tab.left{left:0;border-left:none;border-radius:0 14px 14px 0}
    .tab.drag{transition:none;opacity:.85}
    .tab img{width:32px;height:32px;pointer-events:none}
    .badge{position:absolute;top:-7px;background:#5fd38a;color:#000;font:800 11px/16px Arial;border-radius:9px;padding:0 6px;min-width:18px;text-align:center}
    .tab.right .badge{left:-7px}.tab.left .badge{right:-7px}
    .dim{position:fixed;inset:0;background:#0009;pointer-events:auto;display:none}
    .panel{position:fixed;top:0;bottom:0;width:min(370px,93vw);background:#15171a;color:#d9dde2;display:flex;flex-direction:column;
           pointer-events:auto;transition:transform .2s ease;box-shadow:0 0 24px #000c;font-size:13px}
    .panel.right{right:0;border-left:1px solid #3a4047;transform:translateX(105%)}.panel.left{left:0;border-right:1px solid #3a4047;transform:translateX(-105%)}
    .open .panel{transform:none}.open .dim{display:block}
    .ph{padding:10px 12px 8px;border-bottom:1px solid #2f343a;padding-top:max(10px,env(safe-area-inset-top))}
    .row{display:flex;align-items:center;gap:6px}
    .x{margin-left:auto;font-size:22px;color:#8a929c;background:none;border:none;padding:4px 8px}
    .chip{background:#24282d;border:1px solid #3a4047;color:#d9dde2;border-radius:13px;padding:5px 11px;font-size:12px;font-weight:600}
    .chip.on{background:#22304a;border-color:#7aa7ff;color:#fff}
    .list{flex:1;overflow-y:auto;padding:8px;-webkit-overflow-scrolling:touch}
    .card{background:#1d2024;border:1px solid #2f343a;border-radius:10px;padding:8px 10px;margin-bottom:7px;display:flex;align-items:center;gap:10px}
    .card.off{opacity:.62}
    .flag{width:4px;align-self:stretch;border-radius:3px;background:transparent}
    .nm{color:#6aa9ff;font-weight:700;text-decoration:none}
    .m{color:#8a929c;font-size:12px}.b{color:#fff;font-weight:700}
    .atk{margin-left:auto;background:#7a2530;border:1px solid #ff6b6b;color:#fff;font-weight:800;border-radius:9px;padding:11px 13px;font-size:13px;text-decoration:none;white-space:nowrap}
    .atk.off{background:#24282d;border-color:#3a4047;color:#8a929c}
    .more{background:none;border:none;color:#8a929c;font-size:18px;padding:4px 6px}
    .foot{padding:8px 10px;border-top:1px solid #2f343a;display:flex;gap:6px;padding-bottom:max(8px,env(safe-area-inset-bottom))}
    .sheet{padding:10px;border-top:1px solid #2f343a;background:#1a1c20;display:none}.sheet.show{display:block}
    input,textarea,select{background:#24282d;border:1px solid #3a4047;color:#e6e9ed;border-radius:8px;padding:8px;font-size:14px;width:100%}
    textarea{min-height:70px;font-family:Menlo,monospace;font-size:11px}
    .btn{background:#2b4a85;border:1px solid #3d5fa3;color:#fff;border-radius:8px;padding:8px 12px;font-weight:700;font-size:13px}
    .btn.grey{background:#24282d;border-color:#3a4047}
    .note{color:#e8c547;font-size:12px}
    .msg{font-size:12px;color:#8a929c;margin-top:6px}.err{color:#ff6b6b}
    table.tos{font-size:11px;color:#aab2bc;border-collapse:collapse;margin-top:6px}table.tos td{border-top:1px solid #2f343a;padding:4px;vertical-align:top}
    .empty{color:#8a929c;text-align:center;padding:30px 10px;line-height:1.5}
    h4{margin:10px 0 6px;font-size:13px;color:#fff}
    a.lnk{color:#7aa7ff}
  </style>
  <div class="wrap"><div class="dim"></div><div class="panel"></div><div class="tab"><span class="badge"></span><img alt="Strike List"></div></div>`;
  const wrap = root.querySelector(".wrap"), tab = root.querySelector(".tab"), panel = root.querySelector(".panel");
  const badge = root.querySelector(".badge");
  root.querySelector(".tab img").src = ICON_IMG;
  root.querySelector(".dim").addEventListener("click", () => setOpen(false));

  let open = false, sheet = null, sheetArg = null;
  function setOpen(v) {
    open = v; wrap.classList.toggle("open", v);
    tab.style.display = v ? "none" : "";
    if (v) { sheet = null; render(); if (now() - lastRefresh > 120) refreshAll(false); }
  }

  // ---- draggable edge tab: slides up/down, snaps to the nearest side on release
  function placeTab() {
    tab.classList.toggle("right", cfg.side === "right"); tab.classList.toggle("left", cfg.side === "left");
    tab.style.left = cfg.side === "left" ? "0px" : ""; tab.style.right = cfg.side === "right" ? "0px" : "";
    tab.style.top = `calc(${Math.min(92, Math.max(4, cfg.top))}vh - 31px)`;
    panel.className = "panel " + cfg.side;
  }
  let drag = null;
  tab.addEventListener("pointerdown", (e) => {
    drag = { x0: e.clientX, y0: e.clientY, moved: false };
    try { tab.setPointerCapture(e.pointerId); } catch (err) { /* older WebViews */ }
  });
  tab.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (!drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 8) return;
    drag.moved = true; tab.classList.add("drag");
    tab.style.right = ""; tab.style.left = Math.min(window.innerWidth - 46, Math.max(0, e.clientX - 23)) + "px";
    tab.style.top = Math.min(window.innerHeight - 62, Math.max(0, e.clientY - 31)) + "px";
  });
  let justDragged = false;
  tab.addEventListener("pointerup", (e) => {
    if (!drag) return;
    const d = drag; drag = null; tab.classList.remove("drag");
    if (!d.moved) return;                 // a plain tap opens via "click" below
    justDragged = true; setTimeout(() => (justDragged = false), 350);
    cfg.side = e.clientX < window.innerWidth / 2 ? "left" : "right";
    cfg.top = Math.round((e.clientY / window.innerHeight) * 100);
    saveCfg(); placeTab();
  });
  // open on click (not pointerup): otherwise the same tap's click lands on the backdrop and closes it again
  tab.addEventListener("click", () => { if (!justDragged) setOpen(true); });
  tab.addEventListener("pointercancel", () => { drag = null; tab.classList.remove("drag"); placeTab(); });

  // ---- panel rendering
  function rows() {
    let r = Object.keys(targets).map(row);
    if (cfg.filter === "ok") r = r.filter((x) => x.st.ok);
    const key = { fr: (x) => -(x.fr ?? -1), ff: (x) => -(effFF(x) ?? -1), level: (x) => -(x.level || 0),
                  status: (x) => (x.st.ok ? -1 : x.st.left || 1e9) }[cfg.sort];
    return r.sort((a, b) => key(a) - key(b));
  }
  function render() {
    const all = Object.keys(targets).map(row);
    const ready = all.filter((x) => x.st.ok).length;
    badge.textContent = ready; badge.style.display = ready ? "" : "none";
    if (!open) return;
    const list = rows();
    const sorts = { fr: "FR", ff: "FF", level: "Lvl", status: "Status" };
    let html = `<div class="ph"><div class="row"><img src="${HEADER_IMG}" style="height:34px"><button class="x" data-a="close">✕</button></div>
      <div class="row" style="margin-top:8px">
        <button class="chip ${cfg.filter === "ok" ? "on" : ""}" data-a="f-ok">Attackable ${ready}</button>
        <button class="chip ${cfg.filter === "all" ? "on" : ""}" data-a="f-all">All ${all.length}</button>
        <button class="chip" data-a="sort">${sorts[cfg.sort]} ▾</button>
        <button class="chip" style="margin-left:auto" data-a="refresh">${busy ? "…" : "⟳"}</button></div>
      <div class="msg ${lastError ? "err" : ""}">${esc(lastError || lastInfo || (lastRefresh ? "Updated " + ago(lastRefresh) : "Tap ⟳ to load your targets"))}</div></div>`;
    html += `<div class="list">`;
    if (!all.length) html += `<div class="empty">No targets yet.<br>Tap <b>+ Add</b>, use the <b>+ Strike List</b> button on a player's profile,<br>or import a share code in ⚙.</div>`;
    else if (!list.length) html += `<div class="empty">Nobody attackable right now.<br>Tap <b>All</b> to see everyone.</div>`;
    for (const x of list) {
      const flag = FLAG_COLORS[x.t.color || 0];
      const lowNow = x.ffNow != null && x.ffNow < cfg.minFF;
      const lowHit = x.ffNow == null && x.ff != null && x.ff < cfg.minFF;
      html += `<div class="card ${x.st.ok ? "" : "off"}"><div class="flag" style="background:${flag || "transparent"}"></div>
        <div style="min-width:0;flex:1">
          <div><a class="nm" href="${PROFILE_URL(x.id)}">${esc(x.name)}</a> <span class="m">· ${x.level ?? "?"}</span></div>
          <div class="m">FF <span style="${lowHit ? "color:#ff6b6b" : ""}">${fmtFF(x.ff)}</span>${cfg.ffs ? ` · now <span style="${lowNow ? "color:#ff6b6b" : ""}">${fmtFF(x.ffNow)}</span>` : ""} · FR <span class="b">${x.fr != null ? x.fr.toFixed(2) : "–"}</span>${x.bsh ? ` · ~${esc(x.bsh)}` : ""}</div>
          <div style="font-size:12px;font-weight:700;color:${x.st.col}" data-until="${x.id}">${esc(x.st.text)}</div>
          ${x.t.note ? `<div class="note">“${esc(x.t.note)}”</div>` : ""}
        </div>
        <button class="more" data-a="more" data-id="${x.id}">⋯</button>
        ${x.st.ok ? `<a class="atk" href="${ATTACK_URL(x.id)}">Attack</a>` : `<a class="atk off" href="${ATTACK_URL(x.id)}">${x.st.left ? hms(x.st.left) : "Attack"}</a>`}
      </div>`;
    }
    html += `</div>${sheetHtml()}<div class="foot"><button class="chip" data-a="add">+ Add</button><button class="chip" data-a="clean">Clean up</button>
      <button class="chip" data-a="share">⇄ Share</button><button class="chip" style="margin-left:auto" data-a="settings">⚙</button></div>`;
    panel.innerHTML = html;
  }

  function sheetHtml() {
    if (!sheet) return "";
    if (sheet === "add") return `<div class="sheet show"><h4>Add targets</h4>
      <input id="addIds" placeholder="Player IDs or profile links"><div class="row" style="margin-top:8px">
      <button class="btn" data-a="do-add">Add</button><button class="btn grey" data-a="close-sheet">Cancel</button></div></div>`;
    if (sheet === "more") {
      const t = targets[sheetArg] || {};
      return `<div class="sheet show"><h4>${esc(t.name || "#" + sheetArg)}</h4>
      <input id="noteBox" placeholder="Note" value="${esc(t.note || "")}">
      <div class="row" style="margin-top:8px;flex-wrap:wrap">
        <button class="btn grey" data-a="flag">Flag: ${["none", "green", "yellow", "red"][t.color || 0]}</button>
        <button class="btn" data-a="save-note">Save note</button>
        <button class="btn grey" data-a="remove" style="color:#ff6b6b">Remove</button>
        <button class="btn grey" data-a="close-sheet">Close</button></div></div>`;
    }
    if (sheet === "clean") {
      const c = Object.keys(targets).map(row).filter((x) => effFF(x) != null && effFF(x) < cfg.minFF && !(cfg.keepFlagged && x.t.color));
      return `<div class="sheet show"><h4>Clean up — below FF ${cfg.minFF.toFixed(2)}</h4>
      <div class="row"><span class="m">Min FF</span><input id="minFF" type="number" step="0.1" min="1" max="3" value="${cfg.minFF}" style="width:80px">
      <label class="m"><input type="checkbox" id="keepFl" ${cfg.keepFlagged ? "checked" : ""} style="width:auto"> keep flagged</label></div>
      <div class="msg">${c.length ? c.map((x) => `${esc(x.name)} (FF ${fmtFF(effFF(x))})`).join(", ") : "Nothing below your minimum."}</div>
      <div class="row" style="margin-top:8px"><button class="btn grey" data-a="clean-apply">Update</button>
      ${c.length ? `<button class="btn" style="background:#7a2530;border-color:#ff6b6b" data-a="clean-go">Remove ${c.length}</button>` : ""}
      <button class="btn grey" data-a="close-sheet">Close</button></div></div>`;
    }
    if (sheet === "share") return `<div class="sheet show"><h4>Share your list (any phone ⇄ any computer)</h4>
      <div class="m">Copy this code and paste it into Strike List on another device — desktop app (Targets → Share) or this script.</div>
      <textarea id="codeOut" readonly>${esc(exportCode())}</textarea>
      <div class="row" style="margin-top:6px"><button class="btn" data-a="copy">Copy code</button></div>
      <h4>Import</h4><textarea id="codeIn" placeholder="Paste a Strike List code (or player IDs)"></textarea>
      <div class="row" style="margin-top:6px"><button class="btn" data-a="import">Import</button><button class="btn grey" data-a="close-sheet">Close</button></div>
      <div class="msg" id="shareMsg"></div></div>`;
    if (sheet === "settings") return `<div class="sheet show" style="max-height:60vh;overflow:auto"><h4>Settings</h4>
      ${pdaKeyValid ? `<div class="m">API key: provided by Torn PDA ✓</div>` :
        `<div class="m">Torn API key (Limited Access)</div><input id="keyBox" type="password" value="${esc(cfg.key)}" placeholder="16-character key">`}
      <label class="m" style="display:block;margin-top:8px"><input type="checkbox" id="ffsOn" ${cfg.ffs ? "checked" : ""} style="width:auto"> Use FF Scouter (FF now + estimated stats)</label>
      <div class="m">FF Scouter is a separate service — turning it on sends your API key and the player IDs you look up to ffscouter.com. Read their terms first: <a class="lnk" href="https://ffscouter.com/" target="_blank">ffscouter.com</a></div>
      <div class="row" style="margin-top:8px"><button class="btn" data-a="save-settings">Save</button><button class="btn grey" data-a="reset-tab">Reset tab position</button><button class="btn grey" data-a="close-sheet">Close</button></div>
      <div class="msg" id="setMsg">${esc(setMsg)}</div>
      <h4>How your key is used</h4><table class="tos">
        <tr><td><b>Data storage</b></td><td>Only on this device</td></tr>
        <tr><td><b>Data sharing</b></td><td>Nobody — except ffscouter.com if you turn FF Scouter on</td></tr>
        <tr><td><b>Purpose</b></td><td>Your personal target list</td></tr>
        <tr><td><b>Key storage</b></td><td>On this device only${pdaKeyValid ? " (managed by Torn PDA)" : ""}; never shared except with FF Scouter if on</td></tr>
        <tr><td><b>Access level</b></td><td>Limited</td></tr></table>
      <div class="msg">Strike List Mobile ${VERSION} · free software (GPL v3) · <a class="lnk" href="https://github.com/PatMcCracken78/StrikeList" target="_blank">source</a> · not affiliated with Torn</div></div>`;
    return "";
  }

  // ---- actions
  const SORT_ORDER = ["fr", "ff", "level", "status"];
  panel.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-a]"); if (!el) return;
    const a = el.dataset.a, $ = (s) => root.querySelector(s);
    if (a === "close") return setOpen(false);
    if (a === "f-ok" || a === "f-all") { cfg.filter = a === "f-ok" ? "ok" : "all"; saveCfg(); return render(); }
    if (a === "sort") { cfg.sort = SORT_ORDER[(SORT_ORDER.indexOf(cfg.sort) + 1) % SORT_ORDER.length]; saveCfg(); return render(); }
    if (a === "refresh") return refreshAll(true);
    if (["add", "clean", "share", "settings"].includes(a)) { sheet = sheet === a ? null : a; setMsg = ""; return render(); }
    if (a === "more") { sheet = "more"; sheetArg = Number(el.dataset.id); return render(); }
    if (a === "close-sheet") { sheet = null; return render(); }
    if (a === "do-add") {
      const ids = parseIds($("#addIds").value); ids.forEach((id) => { if (!targets[id]) targets[id] = { added: now(), color: 0, note: "" }; });
      saveTargets(); sheet = null; render(); for (const id of ids) { try { await refreshTarget(id); } catch (err) { lastError = err.message; } }
      saveTargets(); refreshFFS(ids, false).catch(() => {}).then(render); return;
    }
    if (a === "flag") { const t = targets[sheetArg]; t.color = ((t.color || 0) + 1) % 4; saveTargets(); return render(); }
    if (a === "save-note") { targets[sheetArg].note = $("#noteBox").value.slice(0, 200); saveTargets(); sheet = null; return render(); }
    if (a === "remove") { delete targets[sheetArg]; saveTargets(); sheet = null; return render(); }
    if (a === "clean-apply") { cfg.minFF = Math.min(3, Math.max(1, parseFloat($("#minFF").value) || 2.5)); cfg.keepFlagged = $("#keepFl").checked; saveCfg(); return render(); }
    if (a === "clean-go") {
      Object.keys(targets).map(row).filter((x) => effFF(x) != null && effFF(x) < cfg.minFF && !(cfg.keepFlagged && x.t.color))
        .forEach((x) => delete targets[x.id]);
      saveTargets(); sheet = null; return render();
    }
    if (a === "copy") {
      const box = $("#codeOut"); box.select();
      try { await navigator.clipboard.writeText(box.value); $("#shareMsg").textContent = "Copied ✓ — paste it on your other device."; }
      catch (err) { document.execCommand && document.execCommand("copy"); $("#shareMsg").textContent = "Code selected — long-press to copy if needed."; }
      return;
    }
    if (a === "import") {
      try { const r = importCode($("#codeIn").value); lastError = ""; lastInfo = `Imported: ${r.added} new, ${r.updated} already on your list`; sheet = null; render(); refreshAll(false); }
      catch (err) { $("#shareMsg").textContent = "That code didn't work — copy the whole thing, starting with SL1:"; }
      return;
    }
    if (a === "reset-tab") { cfg.side = "right"; cfg.top = 45; saveCfg(); placeTab(); return; }
    if (a === "save-settings") {
      if (!pdaKeyValid) cfg.key = ($("#keyBox").value || "").trim();
      const want = $("#ffsOn").checked; saveCfg();
      if (want && !cfg.ffs) {
        setMsg = "Checking your key with FF Scouter…"; render();
        try {
          const ck = await crossGet(`${FFS}/api/v1/check-key?key=${apiKey()}`);
          if (!ck.is_registered) {
            const r = await crossPost(`${FFS}/api/v1/register`, { key: apiKey(), agree_to_data_policy: true, signup_source: "StrikeList" });
            if (!(r.success || r.code === 8)) throw new Error(r.error || "registration failed");
          }
          cfg.ffs = true; saveCfg(); setMsg = "Saved ✓ FF Scouter on"; render(); refreshFFS(Object.keys(targets).map(Number), true).then(render, (err) => { lastError = err.message; render(); });
        } catch (err) { setMsg = "FF Scouter: " + err.message; render(); }
      } else { cfg.ffs = want; saveCfg(); setMsg = "Saved ✓"; render(); }
      return;
    }
  });

  // ---- live countdowns (every second, only the status lines)
  setInterval(() => {
    if (!open) { if (now() % 10 === 0) render(); return; }
    root.querySelectorAll("[data-until]").forEach((el) => {
      const t = targets[el.dataset.until]; if (!t) return; const s = statusOf(t);
      el.textContent = s.text; el.style.color = s.col;
    });
  }, 1000);
  // background: keep statuses fresh while Torn is open (every 5 min, only when the page is visible)
  setInterval(() => { if (!document.hidden && apiKey() && Object.keys(targets).length) refreshAll(false); }, 5 * 60 * 1000);

  // ---- "+ Strike List" button on player profiles
  function profileButton() {
    const m = location.href.match(/profiles\.php\?XID=(\d+)/); if (!m) return;
    const id = Number(m[1]); if (document.getElementById("sl-profile-btn")) return;
    const anchor = document.querySelector("#skip-to-content, .content-title h4, h4");
    if (!anchor) return;
    const b = document.createElement("span");
    b.id = "sl-profile-btn";
    b.style.cssText = "display:inline-flex;align-items:center;gap:6px;margin:6px 0 6px 8px;padding:4px 10px;border-radius:12px;" +
                      "background:#22304a;border:1px solid #7aa7ff;color:#fff;font:600 12px Arial;cursor:pointer;vertical-align:middle";
    const paint = () => {
      const on = !!targets[id], f = ffs[id];
      b.innerHTML = `<img src="${ICON_IMG}" style="width:16px;height:16px">${on ? "✓ On Strike List" : "+ Strike List"}` +
                    (f && f.ff != null ? `<span style="color:#aab2bc;font-weight:400">· FF ${fmtFF(f.ff)}${f.bsh ? " · ~" + esc(f.bsh) : ""}</span>` : "");
    };
    b.addEventListener("click", async () => {
      if (targets[id]) { delete targets[id]; saveTargets(); paint(); render(); return; }
      targets[id] = { added: now(), color: 0, note: "" }; saveTargets(); paint(); render();
      try { await refreshTarget(id); saveTargets(); render(); } catch (e) { /* shown later in panel */ }
    });
    anchor.insertAdjacentElement("afterend", b);
    paint();
    if (cfg.ffs && !ffs[id]) refreshFFS([id], false).then(paint, () => {});
  }
  new MutationObserver(() => profileButton()).observe(document.body, { childList: true, subtree: true });
  window.addEventListener("hashchange", profileButton);

  // ------------------------------------------------------------------ start
  placeTab();
  render();
  profileButton();
  window.__strikeListMobileAPI = { exportCode, importCode, targets: () => targets }; // handy for debugging
})();
