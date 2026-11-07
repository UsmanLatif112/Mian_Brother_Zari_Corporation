(function () {
  const sidebar = document.getElementById("erp-sidebar");
  let backdrop = document.getElementById("sidebar-backdrop");
  if (!backdrop) {
    backdrop = document.createElement("div");
    backdrop.id = "sidebar-backdrop";
    backdrop.className = "sidebar-backdrop";
    document.body.appendChild(backdrop);
  }

  function setSidebarOpen(open) {
    sidebar?.classList.toggle("open", open);
    sidebar?.classList.toggle("show", open);
    backdrop.classList.toggle("show", open);
    // Only lock body scroll while the drawer is open; always restore afterward
    if (open) {
      document.body.dataset.portalScrollY = String(window.scrollY || 0);
      document.body.style.overflow = "hidden";
      document.body.style.position = "fixed";
      document.body.style.width = "100%";
      document.body.style.top = `-${document.body.dataset.portalScrollY}px`;
    } else {
      const y = Number(document.body.dataset.portalScrollY || 0);
      document.body.style.overflow = "";
      document.body.style.position = "";
      document.body.style.width = "";
      document.body.style.top = "";
      delete document.body.dataset.portalScrollY;
      window.scrollTo(0, y);
    }
  }

  document.getElementById("sidebar-toggle")?.addEventListener("click", () => {
    const open = !(sidebar?.classList.contains("open") || sidebar?.classList.contains("show"));
    setSidebarOpen(open);
  });
  backdrop.addEventListener("click", () => setSidebarOpen(false));
  sidebar?.querySelectorAll("a.nav-link").forEach((link) => {
    link.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 991.98px)").matches) setSidebarOpen(false);
    });
  });

  // Table search is handled by portal-datatables.js when DataTables is present.

  function syncCustomDates() {
    const form = document.getElementById("portal-period-form");
    if (!form) return;
    const period = form.querySelector('[name="period"]')?.value || "all";
    const show = period === "custom";
    form.querySelectorAll(".custom-date-field").forEach((el) => {
      el.style.display = show ? "" : "none";
    });
  }

  window.onPortalPeriodChange = function onPortalPeriodChange() {
    const form = document.getElementById("portal-period-form");
    if (!form) return;
    const period = form.querySelector('[name="period"]')?.value || "all";
    syncCustomDates();
    if (period !== "custom") form.submit();
  };

  syncCustomDates();

  document.addEventListener("click", (event) => {
    const btn = event.target.closest(".password-toggle-btn");
    if (!btn) return;
    const group = btn.closest(".input-group");
    const input = group?.querySelector('input[type="password"], input[type="text"]');
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    btn.setAttribute("title", show ? "Hide password" : "Show password");
    const icon = btn.querySelector("i");
    if (icon) {
      icon.className = show ? "fa-solid fa-eye-slash" : "fa-solid fa-eye";
    }
  });
})();
