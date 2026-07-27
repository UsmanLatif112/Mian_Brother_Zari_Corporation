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

  document.getElementById('page-refresh-btn')?.addEventListener('click', (e) => {
    const btn = e.currentTarget;
    btn.classList.add('is-refreshing');
    btn.disabled = true;
    document.getElementById('global-loader')?.classList.remove('d-none');
    // Hard reload so server-rendered stats and tables refresh
    window.location.reload();
  });

  // Remember GET filters per page until the user clears them (All / Clear).
  (function persistPageFilters() {
    const storagePrefix = 'erp-page-filters:';

    function formFieldNames(form) {
      return [...new Set([...form.elements].map((el) => el.name).filter(Boolean))];
    }

    function isDefaultFilter(saved) {
      if (!saved || typeof saved !== 'object') return true;
      const period = saved.period;
      const category = saved.category_id;
      const start = saved.start_date || saved.start;
      const end = saved.end_date || saved.end;
      const periodDefault = !period || period === 'all';
      const categoryDefault = !category;
      const datesDefault = !start && !end;
      return periodDefault && categoryDefault && datesDefault;
    }

    function readSaved(key) {
      try {
        return JSON.parse(localStorage.getItem(storagePrefix + key) || 'null');
      } catch (_) {
        return null;
      }
    }

    function writeSaved(key, values) {
      if (isDefaultFilter(values)) {
        localStorage.removeItem(storagePrefix + key);
        return;
      }
      localStorage.setItem(storagePrefix + key, JSON.stringify(values));
    }

    document.querySelectorAll('[data-clear-page-filters]').forEach((el) => {
      el.addEventListener('click', () => {
        const key = el.dataset.clearPageFilters;
        if (key) localStorage.removeItem(storagePrefix + key);
      });
    });

    const form = document.querySelector('form[data-persist-filters]');
    if (!form) return;

    const key = form.dataset.persistFilters;
    if (!key) return;

    const fields = formFieldNames(form);
    const url = new URL(window.location.href);
    // Any filter query key present (even empty / "all") means user applied a filter this visit
    const hasFilterInUrl = fields.some((name) => url.searchParams.has(name));

    if (hasFilterInUrl) {
      const values = {};
      fields.forEach((name) => {
        if (url.searchParams.has(name)) values[name] = url.searchParams.get(name) || '';
      });
      writeSaved(key, values);
      return;
    }

    const saved = readSaved(key);
    if (isDefaultFilter(saved)) return;

    let changed = false;
    Object.entries(saved).forEach(([name, value]) => {
      if (value == null || value === '') return;
      if (url.searchParams.get(name) === String(value)) return;
      url.searchParams.set(name, String(value));
      changed = true;
    });
    if (changed) {
      window.location.replace(url.pathname + '?' + url.searchParams.toString());
    }
  })();

  const searchScope = document.body.dataset.searchScope || 'none';
  const searchMode = document.body.dataset.searchMode || 'none';
  const searchTableSel = document.body.dataset.searchTable || '';
  const pageTableSearch = searchMode === 'table' && searchTableSel;
  const pageApiSearch = searchMode === 'api' && searchScope && searchScope !== 'none';

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
      // When navbar owns search for this page, hide DataTables' own filter.
      dom: pageTableSearch ? dtDomNoFilter : dtDomWithFilter,
    });
  }

  function escapeHtml(text) {
    const el = document.createElement('div');
    el.textContent = text == null ? '' : String(text);
    return el.innerHTML;
  }

  function showErpToast(category, message, delayMs) {
    const container = document.getElementById('toast-container');
    if (!container || !message) return;
    const bg = {
      success: 'text-bg-success',
      danger: 'text-bg-danger',
      warning: 'text-bg-warning',
      info: 'text-bg-primary',
    }[category] || 'text-bg-secondary';
    const toast = document.createElement('div');
    toast.className = `toast align-items-center ${bg} border-0`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    toast.innerHTML =
      `<div class="d-flex"><div class="toast-body">${escapeHtml(message)}</div>` +
      '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>';
    container.appendChild(toast);
    const bsToast = new bootstrap.Toast(toast, { autohide: true, delay: delayMs || 5000 });
    toast.addEventListener('hidden.bs.toast', () => toast.remove());
    bsToast.show();
  }

  window.showErpToast = showErpToast;

  const flashEl = document.getElementById('erp-flash-data');
  if (flashEl) {
    try {
      const messages = JSON.parse(flashEl.textContent);
      messages.forEach(([category, message], i) => {
        setTimeout(() => showErpToast(category, message, 5000), i * 150);
      });
    } catch (_) { /* ignore */ }
  }

  if (document.querySelector('.erp-wrapper')) {
    let toastCursor = parseInt(sessionStorage.getItem('erp-toast-cursor') || '0', 10) || 0;
    let toastReady = sessionStorage.getItem('erp-toast-ready') === '1';

    async function bootstrapToastCursor() {
      if (toastReady) return;
      try {
        const res = await fetch('/api/toasts?after=0', { credentials: 'same-origin' });
        if (!res.ok) return;
        const data = await res.json();
        const items = data.toasts || [];
        if (items.length) {
          const maxId = items.reduce((m, t) => Math.max(m, parseInt(t.id, 10) || 0), 0);
          if (!sessionStorage.getItem('erp-toast-cursor')) {
            toastCursor = maxId;
            sessionStorage.setItem('erp-toast-cursor', String(toastCursor));
          }
        }
      } catch (_) { /* ignore */ }
      sessionStorage.setItem('erp-toast-ready', '1');
      toastReady = true;
    }

    async function pollAppToasts() {
      if (!toastReady) await bootstrapToastCursor();
      try {
        const res = await fetch(`/api/toasts?after=${toastCursor}`, { credentials: 'same-origin' });
        if (!res.ok) return;
        const data = await res.json();
        const items = data.toasts || [];
        items.forEach((t, i) => {
          const id = parseInt(t.id, 10) || 0;
          if (id > toastCursor) toastCursor = id;
          setTimeout(() => showErpToast(t.category || 'info', t.message, 5000), i * 200);
        });
        if (items.length) {
          sessionStorage.setItem('erp-toast-cursor', String(toastCursor));
        }
      } catch (_) { /* offline */ }
    }

    bootstrapToastCursor().then(() => {
      pollAppToasts();
      setInterval(pollAppToasts, 3000);
    });
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

  if (searchInput && searchMode === 'none') {
    searchInput.disabled = true;
    searchInput.setAttribute('title', 'Search is not available on this page');
  }

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
    results?.classList.add('d-none');

    // Table pages: filter only this page's DataTable — never call cross-module API.
    if (pageTableSearch) {
      const dt = getPageDataTable();
      if (dt) dt.search(q).draw();
      return;
    }

    if (!pageApiSearch) {
      return;
    }

    if (q.length < 2) {
      return;
    }
    timer = setTimeout(async () => {
      const res = await fetch(
        '/api/search?q=' + encodeURIComponent(q) + '&scope=' + encodeURIComponent(searchScope)
      );
      const data = await res.json();
      if (!results) return;
      if (!data.results?.length) {
        results.innerHTML = '<div class="lookup-item text-muted">No results</div>';
        results.classList.remove('d-none');
        return;
      }
      results.innerHTML = data.results
        .map(
          (r) =>
            `<a class="lookup-item search-result-item" href="${r.url}" role="option">${r.label} <small class="text-muted">(${r.type})</small></a>`
        )
        .join('');
      results.classList.remove('d-none');
      results.setAttribute('role', 'listbox');
    }, 250);
  });

  // Deep-link from dashboard global search: ?search=term filters this page's table.
  (function applySearchFromQuery() {
    const term = new URLSearchParams(window.location.search).get('search');
    if (!term || !searchInput) return;
    searchInput.value = term;
    if (pageTableSearch) {
      const dt = getPageDataTable();
      if (dt) dt.search(term).draw();
    } else if (pageApiSearch && term.trim().length >= 2) {
      searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
  })();

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

  // Arrow Up/Down + Enter for all search lookup dropdowns
  function findLookupPanel(input) {
    if (!(input instanceof HTMLElement)) return null;
    const scopes = [
      input.closest('.position-relative'),
      input.closest('td'),
      input.closest('[class*="col-"]'),
      input.closest('.global-search'),
      input.parentElement,
      input.parentElement?.parentElement,
    ].filter(Boolean);
    for (const scope of scopes) {
      const panel =
        scope.querySelector('.lookup-results') ||
        scope.querySelector('#global-search-results') ||
        scope.querySelector('.search-results');
      if (panel && !panel.classList.contains('d-none')) return panel;
    }
    return null;
  }

  function lookupItems(panel) {
    return [...panel.querySelectorAll('button.lookup-item, a.lookup-item, a.search-result-item')].filter(
      (el) => !el.classList.contains('text-muted') && !el.classList.contains('text-danger')
    );
  }

  function setLookupActive(items, index) {
    items.forEach((el, i) => {
      const on = i === index;
      el.classList.toggle('is-active', on);
      if (on) {
        el.setAttribute('aria-selected', 'true');
        el.scrollIntoView({ block: 'nearest' });
      } else {
        el.removeAttribute('aria-selected');
      }
    });
  }

  document.addEventListener('keydown', (e) => {
    const input = e.target;
    if (!(input instanceof HTMLInputElement) && !(input instanceof HTMLTextAreaElement)) return;
    if (input.type === 'hidden') return;

    const panel = findLookupPanel(input);
    if (!panel) return;

    const items = lookupItems(panel);
    if (!items.length) return;

    let idx = items.findIndex((el) => el.classList.contains('is-active'));

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      e.stopPropagation();
      idx = idx < 0 ? 0 : idx < items.length - 1 ? idx + 1 : 0;
      setLookupActive(items, idx);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      e.stopPropagation();
      idx = idx < 0 ? items.length - 1 : idx > 0 ? idx - 1 : items.length - 1;
      setLookupActive(items, idx);
      return;
    }
    if (e.key === 'Enter') {
      // Open highlighted result, or the first one if none highlighted yet.
      const target = idx >= 0 ? items[idx] : items[0];
      if (target) {
        e.preventDefault();
        e.stopPropagation();
        target.click();
      }
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      panel.classList.add('d-none');
      items.forEach((el) => el.classList.remove('is-active'));
    }
  });

  // When lookup HTML is replaced, drop stale active state
  document.addEventListener(
    'input',
    (e) => {
      const panel = findLookupPanel(e.target);
      panel?.querySelectorAll('.is-active').forEach((el) => el.classList.remove('is-active'));
    },
    true
  );
})();
