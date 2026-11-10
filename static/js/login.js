(function () {
  "use strict";
  const form = document.getElementById("login-form");
  const box = document.getElementById("login-error");
  const btn = document.getElementById("login-btn");
  function target(role) {
    const next = new URLSearchParams(location.search).get("next");
    if (next && next.charAt(0) === "/" && next.charAt(1) !== "/" && next.indexOf("\\") === -1) return next;  // same-origin paths only
    return role === "SUPER_ADMIN" ? "/dashboard" : "/scanner";
  }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    box.classList.add("hidden");
    btn.disabled = true;
    try {
      const r = await fetch("/api/v1/auth/login", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: form.username.value, password: form.password.value }),
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok) { location.replace(target(j.data.role)); return; }
      box.textContent = r.status === 429 ? "Too many attempts. Please wait a minute and try again."
        : (j.message || "Login failed.");
      box.classList.remove("hidden");
    } catch (e) {
      box.textContent = "Cannot reach the server.";
      box.classList.remove("hidden");
    } finally { btn.disabled = false; form.password.value = ""; }
  });
})();
