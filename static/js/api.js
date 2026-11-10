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
  window.Exodus = { api, boot, logout, decode, el, message, errText, ready, refresh };
})();
