(function () {
  "use strict";
  const X = window.Exodus, sel = document.getElementById("emp-select"), box = document.getElementById("response-box");
  const search = document.getElementById("emp-search"), hint = document.getElementById("vehicle-hint");
  let byCode = {};
  function radio(v) { return document.querySelector('input[name="arrival"][value="' + v + '"]'); }
  function updateArrival() {
    const e = byCode[sel.value];
    const veh = !!(e && e.has_vehicle);
    radio(veh ? "vehicle" : "walk_in").checked = true;
    hint.textContent = !e ? "" : veh
      ? "Registered vehicle: " + e.plates + ". Choose 'On foot' if they walked in; their parking status is not changed."
      : "No vehicle registered for this employee.";
  }
  function filter() {
    const q = search.value.toLowerCase().trim();
    let first = -1;
    Array.from(sel.options).forEach((o, i) => {
      const hit = o.text.toLowerCase().includes(q);  // option text contains name and employee ID
      o.hidden = !hit; o.disabled = !hit;
      if (hit && first === -1) first = i;
    });
    if (first !== -1) sel.selectedIndex = first;
    updateArrival();
  }
  async function submit(kind) {
    if (!sel.value) return;
    box.classList.remove("hidden");
    const body = { employee_code: sel.value };
    if (kind === "in") body.arrival_type = document.querySelector('input[name="arrival"]:checked').value;
    const res = await X.api("/api/v1/access/employee/check-" + kind, { method: "POST", body: body });
    if (res.ok) {
      const d = res.body.data, t = new Date(d.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      X.message(box, true, d.name + ": checked " + (kind === "in" ? "in (" + (d.arrival_type === "vehicle" ? "by vehicle" : "on foot") + ")" : "out") + " at " + t);
    } else X.message(box, false, X.errText(res));
  }
  X.ready.then(async (s) => {
    if (!s) return;
    const res = await X.api("/api/v1/employees");
    (res.body.data ? res.body.data.employees : []).forEach((e) => {
      byCode[e.employee_code] = e;
      const o = document.createElement("option");
      o.value = e.employee_code; o.textContent = X.decode(e.full_name) + " (" + e.employee_code + ")";
      sel.appendChild(o);
    });
    search.addEventListener("input", filter);
    sel.addEventListener("change", updateArrival);
    updateArrival();
    document.getElementById("btn-in").addEventListener("click", () => submit("in"));
    document.getElementById("btn-out").addEventListener("click", () => submit("out"));
  });
})();
