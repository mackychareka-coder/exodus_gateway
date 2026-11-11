(function () {
  "use strict";
  const X = window.Exodus, $ = (id) => document.getElementById(id);
  const sel = $("emp-select"), box = $("response-box"), search = $("emp-search"), hint = $("vehicle-hint");
  let byCode = {};

  const arrival = () => document.querySelector('input[name="arrival"]:checked').value;
  const setArrival = (v) => { document.querySelector('input[name="arrival"][value="' + v + '"]').checked = true; };
  const vehicles = () => (byCode[sel.value] && byCode[sel.value].vehicles) || [];

  function toggleNewFields() {
    $("veh-select").classList.toggle("hidden", vehicles().length === 0);
    $("veh-none").classList.toggle("hidden", vehicles().length > 0);
    $("veh-new").classList.toggle("hidden", vehicles().length > 0 && $("veh-select").value !== "new");
  }
  function renderVehiclePanel() {
    const e = byCode[sel.value];
    const show = !!e && arrival() === "vehicle";
    $("vehicle-panel").classList.toggle("hidden", !show);
    if (!show) return;
    const vs = vehicles(), vsel = $("veh-select");
    vsel.replaceChildren();
    vs.forEach((v) => {
      const o = document.createElement("option");
      o.value = String(v.id);
      o.textContent = v.plate + (v.model || v.color ? " – " + [v.color, v.model].filter(Boolean).map(X.decode).join(" ") : "");
      vsel.appendChild(o);
    });
    const n = document.createElement("option");
    n.value = "new"; n.textContent = "Another / new vehicle…";
    vsel.appendChild(n);
    vsel.value = vs.length ? String(vs[0].id) : "new";
    toggleNewFields();
  }
  function onEmployeeChange() {
    const e = byCode[sel.value];
    setArrival(e && e.has_vehicle ? "vehicle" : "walk_in");
    hint.textContent = !e ? "" : e.has_vehicle
      ? "On file: " + e.plates + ". Choose 'On foot' if they walked in; their parking status is not changed."
      : "No vehicle on file. If they drove, choose 'By vehicle' and enter the car's details.";
    renderVehiclePanel();
  }
  function filter() {
    const q = search.value.toLowerCase().trim();
    let first = -1;
    Array.from(sel.options).forEach((o, i) => {
      const hit = o.text.toLowerCase().includes(q);  // option text holds name and employee ID
      o.hidden = !hit; o.disabled = !hit;
      if (hit && first === -1) first = i;
    });
    if (first !== -1) sel.selectedIndex = first;
    onEmployeeChange();
  }
  async function loadEmployees(keep) {
    const res = await X.api("/api/v1/employees");
    byCode = {}; sel.replaceChildren();
    (res.body.data ? res.body.data.employees : []).forEach((e) => {
      byCode[e.employee_code] = e;
      const o = document.createElement("option");
      o.value = e.employee_code; o.textContent = X.decode(e.full_name) + " (" + e.employee_code + ")";
      sel.appendChild(o);
    });
    if (keep && byCode[keep]) sel.value = keep;
    onEmployeeChange();
  }
  async function submit(kind) {
    if (!sel.value) return;
    box.classList.remove("hidden");
    const body = { employee_code: sel.value };
    if (kind === "in") {
      body.arrival_type = arrival();
      if (body.arrival_type === "vehicle") {
        if ($("veh-select").value !== "new" && vehicles().length) {
          body.vehicle_id = Number($("veh-select").value);
        } else {
          const plate = $("veh-plate").value.trim();
          if (!plate) { X.message(box, false, "Enter the vehicle's number plate."); return; }
          body.vehicle = { plate: plate };
          if ($("veh-model").value.trim()) body.vehicle.model = $("veh-model").value.trim();
          if ($("veh-color").value.trim()) body.vehicle.color = $("veh-color").value.trim();
        }
      }
    }
    const res = await X.api("/api/v1/access/employee/check-" + kind, { method: "POST", body: body });
    if (!res.ok) { X.message(box, false, X.errText(res)); return; }
    const d = res.body.data, t = new Date(d.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    let how = "";
    if (kind === "in") how = d.arrival_type === "vehicle" ? " (by vehicle" + (d.vehicle ? " " + d.vehicle : "") + (d.vehicle_saved ? ", new vehicle saved to profile" : "") + ")" : " (on foot)";
    X.message(box, true, d.name + ": checked " + (kind === "in" ? "in" : "out") + how + " at " + t);
    if (d.vehicle_saved) { ["veh-plate", "veh-model", "veh-color"].forEach((i) => { $(i).value = ""; }); loadEmployees(sel.value); }
  }
  X.ready.then(async (s) => {
    if (!s) return;
    await loadEmployees();
    search.addEventListener("input", filter);
    sel.addEventListener("change", onEmployeeChange);
    document.querySelectorAll('input[name="arrival"]').forEach((r) => r.addEventListener("change", renderVehiclePanel));
    $("veh-select").addEventListener("change", toggleNewFields);
    $("btn-in").addEventListener("click", () => submit("in"));
    $("btn-out").addEventListener("click", () => submit("out"));
  });
})();
