(function () {
  if (!window.jQuery || !jQuery.fn.dataTable) return;

  const dtDomNoFilter =
    '<"dt-toolbar"<"dt-left"l>>' +
    "t" +
    '<"dt-footer"<"dt-left"i><"dt-right"p>>';

  jQuery(".datatable").each(function initPortalDatatable() {
    const $table = jQuery(this);
    if (jQuery.fn.dataTable.isDataTable($table)) return;
    const emptyTable = $table.data("emptyTable") || "No data available";
    const pageLen = parseInt($table.data("pageLength"), 10);
    $table.DataTable({
      pageLength: Number.isFinite(pageLen) && pageLen > 0 ? pageLen : 25,
      lengthMenu: [
        [10, 25, 50, 100, -1],
        [10, 25, 50, 100, "All"],
      ],
      order: [],
      autoWidth: false,
      language: {
        lengthMenu: "Show _MENU_",
        search: "Search",
        searchPlaceholder: "Filter rows…",
        info: "_START_–_END_ of _TOTAL_",
        infoEmpty: "0 entries",
        infoFiltered: "(filtered from _MAX_)",
        paginate: {
          previous: "‹",
          next: "›",
        },
        zeroRecords: "No matching records",
        emptyTable,
      },
      dom: dtDomNoFilter,
      columnDefs: (function () {
        const actionIdx = $table.find("thead th.col-actions").index();
        return actionIdx >= 0 ? [{ orderable: false, targets: actionIdx }] : [];
      })(),
    });
  });

  const search = document.getElementById("global-search-input");
  if (search) {
    search.addEventListener("input", () => {
      const q = search.value || "";
      jQuery(".datatable").each(function () {
        const $t = jQuery(this);
        if (!jQuery.fn.dataTable.isDataTable($t)) return;
        $t.DataTable().search(q).draw();
      });
    });
  }
})();
