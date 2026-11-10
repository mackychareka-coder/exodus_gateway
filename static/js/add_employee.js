(function () {
  "use strict";
  const X = window.Exodus, $ = (id) => document.getElementById(id), box = $("reg-response-box");
  $("new-emp-has-vehicle").addEventListener("change", () =>
    $("vehicle-section").classList.toggle("hidden", $("new-emp-has-vehicle").value !== "yes"));
  X.ready.then((s) => {
    if (!s) return;
    $("reg-submit").addEventListener("click", async () => {
      const p = { employee_code: $("new-emp-id").value, full_name: $("new-emp-name").value };
      if ($("new-emp-contact").value) p.phone = $("new-emp-contact").value;
      if ($("new-emp-email").value) p.email = $("new-emp-email").value;
      if ($("new-emp-has-vehicle").value === "yes") {
        p.vehicle = { plate: $("veh-reg").value };
        if ($("veh-model").value) p.vehicle.model = $("veh-model").value;
        if ($("veh-color").value) p.vehicle.color = $("veh-color").value;
      }
      box.classList.remove("hidden");
      const r = await X.api("/api/v1/employees", { method: "POST", body: p });
      X.message(box, r.ok, r.ok ? r.body.message : X.errText(r));
      if (r.ok) setTimeout(() => { location.href = "/scanner"; }, 1200);
    });
  });
})();
