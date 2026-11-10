(function () {
  "use strict";
  const X = window.Exodus, sel = document.getElementById("emp-select"), box = document.getElementById("response-box");
  const search = document.getElementById("emp-search");
  function filter() {
    const q = search.value.toLowerCase().trim();
    let first = -1;
    Array.from(sel.options).forEach((o, i) => {
      const hit = o.text.toLowerCase().includes(q);
      o.hidden = !hit; o.disabled = !hit;
      if (hit && first === -1) first = i;
    });
    if (first !== -1) sel.selectedIndex = first;
  }
  async function submit(kind) {
    if (!sel.value) return;
    box.classList.remove("hidden");
    const res = await X.api("/api/v1/access/employee/check-" + kind, { method: "POST", body: { employee_code: sel.value } });
    if (res.ok) {
      const d = res.body.data, t = new Date(d.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      X.message(box, true, d.name + ": checked " + (kind === "in" ? "in" : "out") + " at " + t);
    } else X.message(box, false, X.errText(res));
  }
  X.ready.then(async (s) => {
    if (!s) return;
    const res = await X.api("/api/v1/employees");
    (res.body.data ? res.body.data.employees : []).forEach((e) => {
      const o = document.createElement("option");
      o.value = e.employee_code; o.textContent = e.full_name;
      sel.appendChild(o);
    });
    search.addEventListener("input", filter);
    document.getElementById("btn-in").addEventListener("click", () => submit("in"));
    document.getElementById("btn-out").addEventListener("click", () => submit("out"));
  });
})();
