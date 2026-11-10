(function () {
  "use strict";
  const X = window.Exodus, E = X.el, $ = (id) => document.getElementById(id);
  const hm = (t) => (t ? new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");
  const stamp = (t) => (t ? new Date(t).toLocaleString() : "");

  function td(cls, ...kids) { const c = document.createElement("td"); c.className = cls; kids.forEach((k) => c.append(k)); return c; }
  function rows(tbody, list, cols, empty, build) {
    tbody.replaceChildren();
    if (!list.length) {
      const tr = document.createElement("tr");
      const c = td("text-center py-6 text-gray-500 italic", empty); c.colSpan = cols;
      tr.append(c); tbody.append(tr); return;
    }
    list.forEach((r) => tbody.append(build(r)));
  }
  function tr(...cells) { const r = document.createElement("tr"); r.className = "hover:bg-gray-700/20"; cells.forEach((c) => r.append(c)); return r; }
  const badge = (text, cls) => E("span", "px-2 py-1 rounded text-[10px] font-bold uppercase " + cls, text);

  async function update() {
    const res = await X.api("/api/v1/admin/dashboard");
    if (!res.ok) return;
    const d = res.body.data;
    $("count-in").textContent = d.counts.staff_on_site;
    $("count-visitors").textContent = d.counts.visitors_on_site;

    rows($("attendance-rows"), d.access_logs, 3, "No entries today.", (l) => {
      const open = !l.check_out_time;
      return tr(
        td("py-3", E("span", "text-white font-medium text-sm block", l.full_name),
           E("span", "text-amber-400 font-mono text-xs", l.plates || "N/A")),
        td("py-3", E("span", "text-[10px] text-gray-400 font-mono block", "In " + hm(l.check_in_time) + (open ? "" : " / Out " + hm(l.check_out_time))),
           E("span", "text-[10px] text-gray-500", l.method + (l.gate_location ? " @ " + l.gate_location : ""))),
        td("py-3 text-right", badge(open ? "On site" : (l.check_out_method === "AUTO" ? "Auto-closed" : "Left"), open ? "bg-green-500/10 text-green-400" : "bg-amber-500/10 text-amber-400")));
    });

    rows($("visitor-rows"), d.visitors, 3, "No guests logged.", (v) => {
      const out = v.status === "departed", on = v.status === "on-site";
      const car = v.arrival_type === "vehicle"
        ? td("py-3", E("span", "text-amber-500 font-mono font-bold block text-sm", v.plate || "N/A"),
             E("span", "text-[11px] text-gray-400", [v.vehicle_color, v.vehicle_model].filter(Boolean).join(" ")))
        : td("py-3", E("span", "text-gray-500 italic text-sm", "Pedestrian"));
      return tr(
        td("py-3", E("span", "font-semibold text-white text-sm block", v.full_name),
           E("span", "block text-gray-500 text-[11px]", "ID: " + v.national_id),
           E("span", "block text-gray-400 text-[11px] italic", v.purpose || ""),
           E("span", "block text-gray-500 text-[11px]", v.host_name ? "Host: " + v.host_name : "")),
        car,
        td("py-3 text-right", badge(v.status + (v.auto_closed ? " (auto)" : ""), on ? "bg-cyan-500/20 text-cyan-400" : out ? "bg-gray-800 text-gray-500" : "bg-amber-500/10 text-amber-400"),
           E("span", "block text-[10px] text-gray-400 mt-1 font-mono", v.entry_time ? "In: " + hm(v.entry_time) : ""),
           E("span", "block text-[10px] text-gray-500 font-mono", v.exit_time ? "Out: " + hm(v.exit_time) : "")));
    });

    rows($("vehicle-rows"), d.vehicles, 3, "No vehicle profiles.", (v) =>
      tr(td("py-3 font-medium text-white text-sm", E("span", "", v.full_name)),
         td("py-3 text-gray-300 text-sm", E("span", "", [v.color, v.model].filter(Boolean).join(" ")),
            E("span", "block font-mono text-amber-500 font-bold", v.plate)),
         td("py-3 text-right", badge(v.parked ? "Parked" : "Away", v.parked ? "bg-green-600 text-black" : "bg-gray-700 text-gray-400"))));
  }

  async function loadAudit() {
    const q = new URLSearchParams({ limit: "100" });
    if ($("audit-outcome").value) q.set("outcome", $("audit-outcome").value);
    const res = await X.api("/api/v1/admin/audit-logs?" + q);
    if (!res.ok) return;
    rows($("audit-rows"), res.body.data.logs, 5, "No audit events.", (a) =>
      tr(td("py-2 font-mono text-[11px] text-gray-400", E("span", "", stamp(a.created_at))),
         td("py-2 text-[11px] text-white", E("span", "", a.action)),
         td("py-2", badge(a.outcome, a.outcome === "success" ? "bg-green-500/10 text-green-400" : "bg-red-500/10 text-red-400")),
         td("py-2 text-[11px] text-gray-300", E("span", "", (a.actor_role || "-") + (a.actor_user_id ? " #" + a.actor_user_id : "") + (a.terminal_id ? " T" + a.terminal_id : ""))),
         td("py-2 text-[10px] text-gray-500 font-mono", E("span", "", (a.target_type ? a.target_type + ":" + a.target_id : "") + " " + (a.detail ? JSON.stringify(a.detail) : "")))));
  }

  X.ready.then((s) => {
    if (!s) return;
    setInterval(() => { $("live-clock").textContent = new Date().toLocaleString(); }, 1000);
    update(); setInterval(update, 3000);
    const tab = $("tab-audit");
    if (tab) {
      tab.addEventListener("click", () => { $("audit-panel").classList.toggle("hidden"); loadAudit(); });
      $("audit-outcome").addEventListener("change", loadAudit);
    }
  });
})();
