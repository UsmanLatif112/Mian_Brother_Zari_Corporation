/**
 * Type-in category / subcategory lookup with AJAX create.
 * Usage:
 *   CategoryLookup.bind({
 *     searchId: 'cat-search',
 *     idId: 'category_id',
 *     resultsId: 'cat-results',
 *     parentIdGetter: null, // or () => document.getElementById('category_id').value
 *   });
 */
window.CategoryLookup = (function () {
  function csrf() {
    return window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;
  }

  function headers() {
    return {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf(),
    };
  }

  async function createCategory(name, parentId) {
    const res = await fetch('/api/categories/quick', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ name, parent_id: parentId || null }),
    });
    return res.json();
  }

  function bind(opts) {
    const search = opts.search || document.getElementById(opts.searchId);
    const idInput = opts.idInput || document.getElementById(opts.idId);
    const results = opts.results || document.getElementById(opts.resultsId);
    if (!search || !idInput || !results) return;

    let timer = null;
    const getParentId = typeof opts.parentIdGetter === 'function' ? opts.parentIdGetter : () => null;
    const onChange = typeof opts.onChange === 'function' ? opts.onChange : () => {};
    const label = opts.label || 'category';

    async function runLookup(q) {
      const parentId = getParentId();
      if (opts.requireParent && !parentId) {
        results.innerHTML =
          '<div class="lookup-item text-muted">Select a category first</div>';
        results.classList.remove('d-none');
        return;
      }
      let url = '/api/categories/lookup?q=' + encodeURIComponent(q);
      if (opts.requireParent) {
        url += '&parent_id=' + encodeURIComponent(parentId);
      } else {
        url += '&parent_id=';
      }
      const res = await fetch(url);
      const data = await res.json();
      const rows = data.results || [];
      if (!rows.length) {
        results.innerHTML = `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add "${q}"</button>`;
      } else {
        results.innerHTML =
          rows
            .map(
              (c) =>
                `<button type="button" class="lookup-item" data-id="${c.id}" data-name="${c.name}">${c.label}</button>`
            )
            .join('') +
          `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add new ${label}</button>`;
      }
      results.classList.remove('d-none');
    }

    search.addEventListener('input', () => {
      clearTimeout(timer);
      idInput.value = '';
      onChange();
      const q = search.value.trim();
      if (!q) {
        results.classList.add('d-none');
        return;
      }
      timer = setTimeout(() => runLookup(q), 200);
    });

    search.addEventListener('focus', () => {
      const q = search.value.trim();
      if (q) runLookup(q);
    });

    results.addEventListener('click', async (e) => {
      const btn = e.target.closest('.lookup-item');
      if (!btn) return;
      e.preventDefault();
      if (btn.classList.contains('lookup-create')) {
        const name = (btn.dataset.name || search.value || '').trim();
        if (!name) return;
        const parentId = getParentId();
        if (opts.requireParent && !parentId) {
          results.innerHTML =
            '<div class="lookup-item text-muted">Select a category first</div>';
          return;
        }
        const data = await createCategory(name, parentId);
        if (!data.ok) {
          results.innerHTML = `<div class="lookup-item text-danger">${data.error || 'Failed'}</div>`;
          results.classList.remove('d-none');
          return;
        }
        idInput.value = data.id;
        search.value = data.name;
        results.classList.add('d-none');
        onChange();
        return;
      }
      idInput.value = btn.dataset.id;
      search.value = btn.dataset.name;
      results.classList.add('d-none');
      onChange();
    });
  }

  /** Ensure typed name is created/selected before submit. Returns {ok, id, error}. */
  async function ensureSelected(searchIdOrEl, idIdOrEl, parentId) {
    const search =
      typeof searchIdOrEl === 'string'
        ? document.getElementById(searchIdOrEl)
        : searchIdOrEl;
    const idInput =
      typeof idIdOrEl === 'string' ? document.getElementById(idIdOrEl) : idIdOrEl;
    if (!search || !idInput) return { ok: false, error: 'Missing fields' };
    if (idInput.value) return { ok: true, id: Number(idInput.value) };
    const name = search.value.trim();
    if (!name) return { ok: false, error: 'Category is required' };
    const data = await createCategory(name, parentId || null);
    if (!data.ok) return { ok: false, error: data.error || 'Could not save category' };
    idInput.value = data.id;
    search.value = data.name;
    return { ok: true, id: data.id };
  }

  return { bind, ensureSelected, createCategory };
})();
