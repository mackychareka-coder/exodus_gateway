(function () {
  "use strict";
  const X = window.Exodus, $ = (id) => document.getElementById(id);
  const pad = (n) => String(n).padStart(2, "0");
  const today = () => { const d = new Date(); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); };
  function sync() {
    const custom = $("period").value === "custom";
    $("anchor-wrap").classList.toggle("hidden", custom);
    $("start-wrap").classList.toggle("hidden", !custom);
    $("end-wrap").classList.toggle("hidden", !custom);
  }
  X.ready.then((s) => {
    if (!s) return;
    $("anchor").value = $("start").value = $("end").value = today();
    $("period").addEventListener("change", sync);
    $("dl").addEventListener("click", async () => {
      const q = new URLSearchParams();
      if ($("period").value === "custom") { q.set("start", $("start").value); q.set("end", $("end").value); }
      else { q.set("period", $("period").value); q.set("date", $("anchor").value); }
      const kind = document.querySelector('input[name="kind"]:checked').value;
      const box = $("dl-msg"); box.classList.remove("hidden");
      const r = await X.download("/api/v1/admin/export/" + kind + ".csv?" + q);
      X.message(box, r.ok, r.ok ? "Download started." : X.errText(r));
    });
  });
})();
