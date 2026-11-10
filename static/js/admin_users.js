(function () {
  "use strict";
  const X = window.Exodus, E = X.el, $ = (id) => document.getElementById(id);
  const ROLES = ["GATE_OPERATOR", "SECURITY_GUARD", "EMPLOYEE", "SUPER_ADMIN"];
  let me = null;

  function btn(label, cls, onClick) {
    const b = E("button", "px-3 py-1.5 rounded-lg text-xs font-bold " + cls, label);
    b.addEventListener("click", onClick);
    return b;
  }
  async function act(path, body, okMsg) {
    const r = await X.api(path, { method: "POST", body: body });
    const box = $("list-msg"); box.classList.remove("hidden");
    X.message(box, r.ok, r.ok ? (okMsg || r.body.message) : X.errText(r));
    if (r.ok) load();
    return r;
  }
  function row(u) {
    const self = u.username === me.username;
    const sel = document.createElement("select");
    sel.className = "p-1.5 bg-gray-900 border border-gray-700 rounded-lg text-xs";
    ROLES.forEach((r) => { const o = document.createElement("option"); o.value = r; o.textContent = r; if (r === u.role) o.selected = true; sel.appendChild(o); });
    sel.disabled = self;
    const apply = btn("Apply", "bg-cyan-700 text-white", async () => {
      if (sel.value === u.role) return;
      const body = { role: sel.value };
      if (sel.value === "EMPLOYEE") {
        const code = prompt("Employee ID to link this account to (e.g. EXODUS-12):");
        if (!code) return;
        body.employee_code = code;
      }
      act("/api/v1/admin/users/" + u.id + "/role", body);
    });
    apply.disabled = self;
    const tr = document.createElement("tr");
    const cell = (cls, ...kids) => { const c = document.createElement("td"); c.className = cls; kids.forEach((k) => c.append(k)); return c; };
    const actions = cell("py-2 text-right space-x-2",
      btn("Reset password", "bg-gray-700 text-white", async () => {
        const pw = prompt("New password for " + u.username + " (12+ characters):");
        if (pw) act("/api/v1/admin/users/" + u.id + "/reset-password", { password: pw }, "Password reset.");
      }),
      u.is_active
        ? btn("Deactivate", "bg-red-700 text-white", () => { if (confirm("Deactivate " + u.username + "?")) act("/api/v1/admin/users/" + u.id + "/deactivate"); })
        : btn("Activate", "bg-green-700 text-white", () => act("/api/v1/admin/users/" + u.id + "/activate")));
    actions.querySelectorAll("button").forEach((b, i) => { if (self && i === 1) b.disabled = true; });
    tr.append(
      cell("py-2 font-medium", E("span", "", u.username + (self ? " (you)" : ""))),
      cell("py-2", sel, document.createTextNode(" "), apply),
      cell("py-2 text-gray-300", E("span", "", u.employee_code ? u.employee_code + " – " + (u.employee_name || "") : "-")),
      cell("py-2", E("span", "text-xs font-bold " + (u.is_active ? "text-green-400" : "text-red-400"), u.is_active ? "Active" : "Disabled")),
      cell("py-2 text-xs text-gray-400", E("span", "", u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "never")),
      actions);
    return tr;
  }
  async function load() {
    const r = await X.api("/api/v1/admin/users");
    const body = $("user-rows"); body.replaceChildren();
    if (r.ok) r.body.data.users.forEach((u) => body.append(row(u)));
  }
  X.ready.then(async (s) => {
    if (!s) return;
    me = s;
    const emps = await X.api("/api/v1/employees");
    (emps.body.data ? emps.body.data.employees : []).forEach((e) => {
      const o = document.createElement("option"); o.value = e.employee_code; o.textContent = X.decode(e.full_name) + " (" + e.employee_code + ")";
      $("nu-employee").appendChild(o);
    });
    $("nu-role").addEventListener("change", () => $("nu-employee").classList.toggle("hidden", $("nu-role").value !== "EMPLOYEE"));
    $("nu-submit").addEventListener("click", async () => {
      const p = { username: $("nu-username").value, password: $("nu-password").value, role: $("nu-role").value };
      if (p.role === "EMPLOYEE") p.employee_code = $("nu-employee").value || undefined;
      const r = await X.api("/api/v1/admin/users", { method: "POST", body: p });
      const box = $("nu-msg"); box.classList.remove("hidden");
      X.message(box, r.ok, r.ok ? "Account '" + r.body.data.username + "' created as " + r.body.data.role + "." : X.errText(r));
      if (r.ok) { $("nu-username").value = ""; $("nu-password").value = ""; load(); }
    });
    load();
  });
})();
