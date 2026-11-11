/* Shared client: in-memory access token, silent refresh via the HttpOnly cookie, safe DOM helpers.
   Tokens are never written to localStorage/sessionStorage. Dynamic text is only ever set via
   textContent (never innerHTML). Free-text fields are stored HTML-escaped, so they are decoded with
   an inert DOMParser and then displayed as text. */
(function () {
  "use strict";
  let accessToken = null, session = null, inflight = null;
  const parser = new DOMParser();

  function decode(v) {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return s.indexOf("&") === -1 ? s : parser.parseFromString(s, "text/html").documentElement.textContent;
  }
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = decode(text);
    return n;
  }
  function toLogin() {
    location.replace("/login?next=" + encodeURIComponent(location.pathname));
  }
  function refresh() {
    if (!inflight) {
      inflight = fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" })
        .then(async (r) => {
          if (!r.ok) throw new Error("auth");
          const j = await r.json();
          accessToken = j.data.access_token;
          session = j.data;
          return session;
        })
        .finally(() => { inflight = null; });
    }
    return inflight;
  }
  async function api(path, opts) {
    opts = opts || {};
    if (!accessToken) { try { await refresh(); } catch (e) { toLogin(); throw e; } }
    const send = () => fetch(path, {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: Object.assign({ Authorization: "Bearer " + accessToken },
                             opts.body ? { "Content-Type": "application/json" } : {}),
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let r = await send();
    if (r.status === 401) {  // access token expired: refresh silently and retry once
      try { await refresh(); } catch (e) { toLogin(); throw e; }
      r = await send();
    }
    let body = {};
    try { body = await r.json(); } catch (e) { /* non-JSON */ }
    return { ok: r.ok, status: r.status, body: body };
  }
  async function download(path) {
    if (!accessToken) { try { await refresh(); } catch (e) { toLogin(); throw e; } }
    const send = () => fetch(path, { credentials: "same-origin", headers: { Authorization: "Bearer " + accessToken } });
    let r = await send();
    if (r.status === 401) { try { await refresh(); } catch (e) { toLogin(); throw e; } r = await send(); }
    if (!r.ok) {
      let body = {}; try { body = await r.json(); } catch (e) { /* not JSON */ }
      return { ok: false, status: r.status, body: body };
    }
    const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = m ? m[1] : "export.csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    return { ok: true, status: 200, body: {} };
  }
  function modal(message, opts) {
    return new Promise((resolve) => {
      const overlay = el("div", "fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4");
      const box = el("div", "bg-gray-800 text-slate-100 border border-gray-600 rounded-2xl p-6 w-full max-w-md space-y-4 shadow-2xl");
      box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
      box.append(el("p", "whitespace-pre-line text-sm", message));
      let input = null;
      if (opts.input) {
        input = document.createElement("input");
        input.type = opts.input; input.placeholder = opts.placeholder || ""; input.maxLength = 128; input.autocomplete = "off";
        input.className = "w-full p-3 bg-gray-900 border border-gray-600 rounded-xl text-sm text-white";
        box.append(input);
      }
      const row = el("div", "flex justify-end gap-3");
      const cancel = el("button", "px-4 py-2 rounded-xl text-sm font-bold bg-gray-700 text-white", "Cancel");
      const ok = el("button", "px-4 py-2 rounded-xl text-sm font-bold text-white " + (opts.danger ? "bg-red-600" : "bg-amber-600"), opts.okLabel || "OK");
      row.append(cancel, ok); box.append(row); overlay.append(box); document.body.append(overlay);
      function done(v) { document.removeEventListener("keydown", onKey); overlay.remove(); resolve(v); }
      function onKey(ev) {
        if (ev.key === "Escape") done(null);
        else if (ev.key === "Enter" && ev.target.tagName !== "BUTTON") ok.click();
      }
      cancel.addEventListener("click", () => done(null));
      ok.addEventListener("click", () => done(input ? input.value : true));
      document.addEventListener("keydown", onKey);
      (input || ok).focus();
    });
  }
  const confirmDialog = (m, o) => modal(m, Object.assign({}, o)).then((v) => v === true);
  const promptDialog = (m, o) => modal(m, Object.assign({ input: "text" }, o)).then((v) => (v === null || v === true ? null : v));
  function toast(ok, text) {
    let c = document.getElementById("toasts");
    if (!c) { c = el("div", "fixed top-4 right-4 z-50 space-y-2 w-80 max-w-[90vw]"); c.id = "toasts"; document.body.appendChild(c); }
    const t = el("div", "p-3 rounded-xl text-sm font-bold shadow-lg text-white " + (ok ? "bg-emerald-600" : "bg-red-600"), text);
    t.setAttribute("role", "status");
    c.appendChild(t);
    setTimeout(() => t.remove(), ok ? 4000 : 10000);
  }
  function deny(msg) {
    document.body.textContent = "";
    const d = el("div", "p-8 text-center text-red-600 font-bold", msg);
    const a = el("a", "block mt-4 underline", "Back to login");
    a.href = "/login";
    d.appendChild(a);
    document.body.appendChild(d);
  }
  async function boot(roles) {
    try { await refresh(); } catch (e) { toLogin(); return null; }
    const isAdmin = session.role === "SUPER_ADMIN";
    if (roles && !isAdmin && roles.indexOf(session.role) === -1) { deny("Your role cannot use this page."); return null; }
    document.querySelectorAll("[data-roles]").forEach((n) => {
      if (!isAdmin && n.dataset.roles.split(" ").indexOf(session.role) === -1) n.remove();
    });
    document.querySelectorAll("[data-logout]").forEach((b) => b.addEventListener("click", logout));
    return session;
  }
  async function logout() {
    try { await fetch("/api/v1/auth/refresh", { method: "DELETE", credentials: "same-origin" }); } catch (e) { /* ignore */ }
    accessToken = null;
    location.replace("/login");
  }
  function message(box, ok, text) {
    box.textContent = decode(text);
    box.className = "p-3 text-sm rounded-xl text-center font-bold mt-3 " +
      (ok ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800");
  }
  function errText(res) {
    const b = res.body || {};
    if (b.details && b.details.length) return b.details.map((d) => d.field + ": " + d.message).join("; ");
    return b.message || "Request failed (" + res.status + ").";
  }
  const roles = document.body.dataset.pageRoles;
  const ready = roles === undefined ? Promise.resolve(null) : boot(roles ? roles.split(" ") : null);
  window.Exodus = { confirm: confirmDialog, prompt: promptDialog, toast, api, download, boot, logout, decode, el, message, errText, ready, refresh };
})();
