(function () {
  const sidebar = document.getElementById("erp-sidebar");
  document.getElementById("sidebar-toggle")?.addEventListener("click", () => {
    sidebar?.classList.toggle("open");
  });

  const search = document.getElementById("global-search-input");
  const table = document.querySelector("table.portal-search-table");
  if (search && table) {
    search.addEventListener("input", () => {
      const q = (search.value || "").trim().toLowerCase();
      table.querySelectorAll("tbody tr").forEach((row) => {
        if (row.querySelector("td[colspan]")) return;
        const text = (row.textContent || "").toLowerCase();
        row.style.display = !q || text.includes(q) ? "" : "none";
      });
    });
  }

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
})();
