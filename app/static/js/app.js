(function () {
  const root = document.documentElement;
  const themeBtn = document.getElementById('theme-toggle');
  const saved = localStorage.getItem('erp-theme') || 'light';
  root.setAttribute('data-theme', saved);
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      localStorage.setItem('erp-theme', next);
    });
  }
  document.getElementById('sidebar-toggle')?.addEventListener('click', () => {
    document.getElementById('erp-sidebar')?.classList.toggle('show');
  });

  const searchScope = document.body.dataset.searchScope || 'global';
  const searchMode = document.body.dataset.searchMode || 'api';
  const searchTableSel = document.body.dataset.searchTable || '';
  const pageTableSearch = searchMode === 'table' && searchTableSel;

  const dtDomWithFilter =
    '<"dt-toolbar"<"dt-left"l><"dt-right"f>>' +
    't' +
    '<"dt-footer"<"dt-left"i><"dt-right"p>>';
  const dtDomNoFilter =
    '<"dt-toolbar"<"dt-left"l>>' +
    't' +
    '<"dt-footer"<"dt-left"i><"dt-right"p>>';

  if (window.jQuery && $('.datatable').length) {
    $('.datatable').DataTable({
      pageLength: 25,
      lengthMenu: [
        [10, 25, 50, 100, -1],
        [10, 25, 50, 100, 'All'],
      ],
      order: [],
      language: {
        lengthMenu: 'Show _MENU_',
        search: 'Search',
        searchPlaceholder: 'Filter rows…',
        info: '_START_–_END_ of _TOTAL_',
        infoEmpty: '0 entries',
        infoFiltered: '(filtered from _MAX_)',
        paginate: {
          previous: '‹',
          next: '›',
        },
        zeroRecords: 'No matching records',
        emptyTable: 'No data available',
      },
      dom: pageTableSearch ? dtDomNoFilter : dtDomWithFilter,
    });
  }

  const flashEl = document.getElementById('erp-flash-data');
  if (flashEl) {
    try {
      const messages = JSON.parse(flashEl.textContent);
      const container = document.getElementById('toast-container');
      messages.forEach(([category, message], i) => {
        const bg = { success: 'text-bg-success', danger: 'text-bg-danger', warning: 'text-bg-warning', info: 'text-bg-primary' }[category] || 'text-bg-secondary';
        const toast = document.createElement('div');
        toast.className = `toast align-items-center ${bg} border-0`;
        toast.setAttribute('role', 'alert');
        toast.innerHTML = `<div class="d-flex"><div class="toast-body">${message}</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
        container?.appendChild(toast);
        const bsToast = new bootstrap.Toast(toast, { delay: 4500 });
        setTimeout(() => bsToast.show(), i * 150);
      });
    } catch (_) { /* ignore */ }
  }

  if (window.OPEN_FORM_MODAL) {
    const modalEl = document.getElementById('formModal');
    if (modalEl && window.bootstrap) {
      bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }
  }
  if (window.location.search.includes('open_modal=')) {
    const url = new URL(window.location.href);
    url.searchParams.delete('open_modal');
    window.history.replaceState({}, '', url.pathname + (url.search || '') + url.hash);
  }

  const searchInput = document.getElementById('global-search-input');
  const results = document.getElementById('global-search-results');
  let timer;

  function getPageDataTable() {
    if (!pageTableSearch || !window.jQuery) return null;
    const el = document.querySelector(searchTableSel);
    if (!el) return null;
    const $t = $(el);
    if (!$.fn.dataTable.isDataTable($t)) return null;
    return $t.DataTable();
  }

  searchInput?.addEventListener('input', () => {
    clearTimeout(timer);
    const q = searchInput.value.trim();
    const dt = getPageDataTable();
    if (dt) {
      results?.classList.add('d-none');
      dt.search(q).draw();
      return;
    }
    if (q.length < 2) {
      results?.classList.add('d-none');
      return;
    }
    timer = setTimeout(async () => {
      const scopeParam = searchScope && searchScope !== 'global' ? `&scope=${encodeURIComponent(searchScope)}` : '';
      const res = await fetch('/api/search?q=' + encodeURIComponent(q) + scopeParam);
      const data = await res.json();
      if (!results) return;
      results.innerHTML =
        data.results
          .map(
            (r) =>
              `<a class="d-block p-2 text-decoration-none" href="${r.url}">${r.label} <small class="text-muted">(${r.type})</small></a>`
          )
          .join('') || '<div class="p-2 text-muted">No results</div>';
      results.classList.remove('d-none');
    }, 250);
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.global-search')) {
      results?.classList.add('d-none');
    }
  });

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-delete-confirm');
    if (!btn) return;
    e.preventDefault();
    const modalEl = document.getElementById('globalConfirmModal');
    const form = document.getElementById('global-confirm-form');
    if (!modalEl || !form) return;
    form.action = btn.dataset.action || '';
    document.getElementById('global-confirm-title').textContent = btn.dataset.title || 'Are you sure?';
    document.getElementById('global-confirm-message').textContent =
      btn.dataset.message || 'This action cannot be undone easily.';
    const submitBtn = document.getElementById('global-confirm-submit');
    submitBtn.textContent = btn.dataset.confirmLabel || 'Delete';
    submitBtn.className = `btn px-4 ${btn.dataset.confirmClass || 'btn-danger'}`;
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  });
})();
