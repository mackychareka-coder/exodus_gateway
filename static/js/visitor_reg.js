(function () {
  "use strict";
  const X = window.Exodus, $ = (id) => document.getElementById(id), box = $("vis-response-box");
  $("vis-arrival-type").addEventListener("change", () =>
    $("vehicle-section").classList.toggle("hidden", $("vis-arrival-type").value !== "vehicle"));
  X.ready.then(async (s) => {
    if (!s) return;
    const res = await X.api("/api/v1/employees");
    (res.body.data ? res.body.data.employees : []).forEach((e) => {
      const o = document.createElement("option");
      o.value = e.employee_code; o.textContent = X.decode(e.full_name) + " (" + e.employee_code + ")";
      $("vis-host").appendChild(o);
    });
    $("vis-submit").addEventListener("click", async () => {
      const p = { full_name: $("vis-name").value, phone: $("vis-contact").value, national_id: $("vis-id").value,
                  purpose: $("vis-purpose").value, arrival_type: $("vis-arrival-type").value,
                  host_employee_code: $("vis-host").value };
      if (p.arrival_type === "vehicle") {
        p.plate = $("vis-veh-reg").value;
        if ($("vis-veh-model").value) p.vehicle_model = $("vis-veh-model").value;
        if ($("vis-veh-color").value) p.vehicle_color = $("vis-veh-color").value;
      }
      box.classList.remove("hidden");
      const r = await X.api("/api/v1/visitors/walk-in", { method: "POST", body: p });
      if (!r.ok) { X.message(box, false, X.errText(r)); return; }
      X.message(box, true, r.body.message + "  Badge: " + r.body.data.badge_code);
      $("badge-qr").src = r.body.data.badge_qr;   // server-generated data: URI
      $("badge-wrap").classList.remove("hidden");
    });
  });
})();
