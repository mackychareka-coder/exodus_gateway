(function () {
  "use strict";
  const X = window.Exodus, sel = document.getElementById("checkout-vis-select"), box = document.getElementById("checkout-response-box");
  X.ready.then(async (s) => {
    if (!s) return;
    const res = await X.api("/api/v1/visitors/on-site");
    (res.body.data ? res.body.data.visitors : []).forEach((v) => {
      const o = document.createElement("option");
      o.value = v.id; o.textContent = X.decode(v.visitor_name) + " (" + v.badge_code + ")";
      sel.appendChild(o);
    });
    document.getElementById("checkout-btn").addEventListener("click", async () => {
      if (!sel.value) return;
      box.classList.remove("hidden");
      const r = await X.api("/api/v1/visitors/check-out", { method: "POST", body: { visit_id: Number(sel.value) } });
      X.message(box, r.ok, r.ok ? r.body.message : X.errText(r));
      if (r.ok) setTimeout(() => { location.href = "/scanner"; }, 1200);
    });
  });
})();
