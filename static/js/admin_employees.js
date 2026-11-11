(function () {
  "use strict";
  const X = window.Exodus, E = X.el, $ = (id) => document.getElementById(id);
  let editing = null, timer = null;

  function flash(ok, text) { X.toast(ok, text); }
  function btn(label, cls, fn) { const b = E("button", "px-3 py-1.5 rounded-lg text-xs font-bold " + cls, label); b.addEventListener("click", fn); return b; }
  const val = (id) => $(id).value.trim();
  const orNull = (v) => (v === "" ? null : v);

  async function load() {
    const q = new URLSearchParams({ status: $("status").value });
    if (val("q")) q.set("q", val("q"));
    const r = await X.api("/api/v1/admin/employees?" + q);
    const body = $("emp-rows"); body.replaceChildren();
    if (!r.ok) { flash(false, X.errText(r)); return; }
    const list = r.body.data.employees;
    if (!list.length) {
      const tr = document.createElement("tr"), td = document.createElement("td");
      td.colSpan = 7; td.className = "text-center py-6 text-gray-500 italic"; td.textContent = "No employees found.";
      tr.append(td); body.append(tr); return;
    }
    list.forEach((e) => body.append(row(e)));
  }
  function row(e) {
    const tr = document.createElement("tr");
    const cell = (cls, ...k) => { const c = document.createElement("td"); c.className = cls; k.forEach((x) => c.append(x)); return c; };
    const actions = cell("py-2 text-right space-x-2",
      btn("Edit", "bg-cyan-700 text-white", () => openEdit(e.employee_code)),
      e.is_active
        ? btn("Deactivate", "bg-amber-700 text-white", async () => { if (await X.confirm("Deactivate " + e.employee_code + "? They can no longer check in, but their history is kept.", { okLabel: "Deactivate" })) patch(e.employee_code, { is_active: false }, "Employee deactivated."); })
        : btn("Activate", "bg-green-700 text-white", () => patch(e.employee_code, { is_active: true }, "Employee activated.")),
      btn("Delete", "bg-red-700 text-white", () => remove(e)));
    tr.append(
      cell("py-2 font-mono text-amber-400", E("span", "", e.employee_code)),
      cell("py-2 font-medium", E("span", "", e.full_name)),
      cell("py-2 text-gray-300", E("span", "", e.phone || "-")),
      cell("py-2 text-gray-300", E("span", "", e.email || "-")),
      cell("py-2 text-amber-500 font-mono text-xs", E("span", "", e.plates || "-")),
      cell("py-2", E("span", "text-xs font-bold " + (e.is_active ? "text-green-400" : "text-red-400"), e.is_active ? "Active" : "Inactive"),
           E("span", "block text-[10px] text-cyan-300", e.on_site ? "On site now" : "")),
      actions);
    return tr;
  }
  async function patch(code, body, okMsg) {
    const r = await X.api("/api/v1/admin/employees/" + encodeURIComponent(code), { method: "PATCH", body: body });
    flash(r.ok, r.ok ? okMsg : X.errText(r));
    if (r.ok) { load(); return r.body.data.employee_code; }
    return null;
  }
  async function remove(e) {
    const name = X.decode(e.full_name);
    if (!(await X.confirm("Delete " + e.employee_code + " (" + name + ")? This cannot be undone.", { danger: true, okLabel: "Delete" }))) return;
    const url = "/api/v1/admin/employees/" + encodeURIComponent(e.employee_code);
    let r = await X.api(url, { method: "DELETE", body: {} });
    if (!r.ok && r.body.code === "HAS_HISTORY") {
      const typed = await X.prompt(r.body.message + "\n\nTo DELETE THEM AND ALL THEIR ACCESS HISTORY, type their Employee ID (" + e.employee_code +
        ") below. To keep the history, cancel and use Deactivate instead.", { danger: true, okLabel: "Delete everything" });
      if (!typed) return;
      r = await X.api(url, { method: "DELETE", body: { purge: true, confirm_code: typed } });
    }
    flash(r.ok, r.ok ? e.employee_code + " deleted." : X.errText(r));
    if (r.ok) { if (editing === e.employee_code) $("edit-panel").classList.add("hidden"); load(); }
  }
  async function openEdit(code) {
    const r = await X.api("/api/v1/admin/employees/" + encodeURIComponent(code));
    if (!r.ok) { flash(false, X.errText(r)); return; }
    const e = r.body.data.employee;
    editing = e.employee_code;
    $("ed-title").textContent = e.employee_code;
    $("ed-code").value = e.employee_code; $("ed-name").value = X.decode(e.full_name);
    $("ed-phone").value = e.phone || ""; $("ed-email").value = e.email || "";
    const box = $("ed-vehicles"); box.replaceChildren();
    if (!r.body.data.vehicles.length) box.append(E("p", "text-xs text-gray-500 italic", "No vehicles registered."));
    r.body.data.vehicles.forEach((v) => {
      const line = E("div", "flex items-center justify-between bg-gray-900 rounded-lg px-3 py-2 text-sm");
      line.append(E("span", "font-mono text-amber-500", v.plate + (v.model || v.color ? "  –  " + [v.color, v.model].filter(Boolean).map(X.decode).join(" ") : "")),
        btn("Remove", "bg-red-700 text-white", async () => {
          if (!(await X.confirm("Remove vehicle " + v.plate + "?", { danger: true, okLabel: "Remove" }))) return;
          const d = await X.api("/api/v1/admin/vehicles/" + v.id, { method: "DELETE" });
          flash(d.ok, d.ok ? "Vehicle removed." : X.errText(d));
          if (d.ok) { openEdit(editing); load(); }
        }));
      box.append(line);
    });
    $("edit-panel").classList.remove("hidden");
    $("edit-panel").scrollIntoView({ behavior: "smooth" });
  }

  X.ready.then((s) => {
    if (!s) return;
    $("ne-submit").addEventListener("click", async () => {
      const p = { employee_code: val("ne-code"), full_name: val("ne-name") };
      if (val("ne-phone")) p.phone = val("ne-phone");
      if (val("ne-email")) p.email = val("ne-email");
      if (val("ne-plate")) p.vehicle = { plate: val("ne-plate") };
      const r = await X.api("/api/v1/employees", { method: "POST", body: p });
      flash(r.ok, r.ok ? "Employee added." : X.errText(r));
      if (r.ok) { ["ne-code", "ne-name", "ne-phone", "ne-email", "ne-plate"].forEach((i) => { $(i).value = ""; }); load(); }
    });
    $("ed-save").addEventListener("click", async () => {
      const body = { full_name: val("ed-name"), phone: orNull(val("ed-phone")), email: orNull(val("ed-email")) };
      if (val("ed-code").toUpperCase() !== editing) body.employee_code = val("ed-code");
      const newCode = await patch(editing, body, "Employee updated.");
      if (newCode) openEdit(newCode);
    });
    $("ed-close").addEventListener("click", () => $("edit-panel").classList.add("hidden"));
    $("ev-add").addEventListener("click", async () => {
      const p = { plate: val("ev-plate") };
      if (val("ev-model")) p.model = val("ev-model");
      if (val("ev-color")) p.color = val("ev-color");
      const r = await X.api("/api/v1/admin/employees/" + encodeURIComponent(editing) + "/vehicles", { method: "POST", body: p });
      flash(r.ok, r.ok ? "Vehicle added." : X.errText(r));
      if (r.ok) { ["ev-plate", "ev-model", "ev-color"].forEach((i) => { $(i).value = ""; }); openEdit(editing); load(); }
    });
    $("q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 300); });
    $("status").addEventListener("change", load);
    load();
  });
})();
