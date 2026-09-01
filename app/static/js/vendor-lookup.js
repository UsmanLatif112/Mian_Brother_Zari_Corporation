/**
 * Searchable vendor lookup + nested quick-add (mirrors customer search on sales).
 *
 * Usage:
 *   VendorLookup.bind({
 *     searchId: 'vendor-search',
 *     idId: 'vendor_id',
 *     resultsId: 'vendor-results',
 *     selectedId: 'vendor-selected',   // optional status text
 *     addBtnId: 'btn-add-vendor',
 *     modalId: 'quickVendorModal',
 *     required: true,
 *   });
 */
(function (global) {
  const csrf = () =>
    global.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;

  function headers() {
    return {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf(),
    };
  }

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  let saveBound = false;
  let activeApply = null;
  let modalBound = false;

  function ensureSaveHandler(modalId) {
    if (saveBound) return;
    saveBound = true;
    document.getElementById('qv-save')?.addEventListener('click', async () => {
      const err = document.getElementById('qv-error');
      if (err) {
        err.classList.add('d-none');
        err.textContent = '';
      }
      const payload = {
        name: document.getElementById('qv-name')?.value.trim() || '',
        phone: document.getElementById('qv-phone')?.value.trim() || '',
        cnic: document.getElementById('qv-cnic')?.value.trim() || '',
        address: document.getElementById('qv-address')?.value.trim() || '',
        opening_balance: document.getElementById('qv-balance')?.value || 0,
        notes: document.getElementById('qv-notes')?.value.trim() || '',
        photo_paths: window.CustomerPhotos?.collectNewPaths?.(document.getElementById(modalId)) || [],
      };
      if (!payload.name) {
        if (err) {
          err.textContent = 'Vendor name is required.';
          err.classList.remove('d-none');
        }
        return;
      }
      try {
        const res = await fetch('/api/vendors/quick', {
          method: 'POST',
          headers: headers(),
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!data.ok) {
          if (err) {
            err.textContent = data.error || 'Could not save vendor';
            err.classList.remove('d-none');
          }
          return;
        }
        if (typeof activeApply === 'function') {
          activeApply(data.id, data.name);
        }
        bootstrap.Modal.getInstance(document.getElementById(modalId))?.hide();
      } catch (_) {
        if (err) {
          err.textContent = 'Could not save vendor';
          err.classList.remove('d-none');
        }
      }
    });
  }

  function ensureModalLifecycle(modalId) {
    if (modalBound) return;
    modalBound = true;
    const qvModal = document.getElementById(modalId);
    qvModal?.addEventListener('hidden.bs.modal', () => {
      if (document.querySelector('.modal.show')) {
        document.body.classList.add('modal-open');
      }
    });
  }

  function openQuickModal(modalId, prefill, applyFn) {
    activeApply = applyFn;
    const modal = document.getElementById(modalId);
    const nameEl = document.getElementById('qv-name');
    const phoneEl = document.getElementById('qv-phone');
    const cnicEl = document.getElementById('qv-cnic');
    const addrEl = document.getElementById('qv-address');
    const balEl = document.getElementById('qv-balance');
    const notesEl = document.getElementById('qv-notes');
    const err = document.getElementById('qv-error');
    if (nameEl) nameEl.value = prefill || '';
    if (phoneEl) phoneEl.value = '';
    if (cnicEl) cnicEl.value = '';
    if (addrEl) addrEl.value = '';
    if (balEl) balEl.value = '0';
    if (notesEl) notesEl.value = '';
    if (err) {
      err.classList.add('d-none');
      err.textContent = '';
    }
    global.CustomerPhotos?.resetField?.(modal);
    bootstrap.Modal.getOrCreateInstance(modal).show();
  }

  function bind(opts) {
    const search = document.getElementById(opts.searchId);
    const idEl = document.getElementById(opts.idId);
    const results = document.getElementById(opts.resultsId);
    const selected = opts.selectedId ? document.getElementById(opts.selectedId) : null;
    const addBtn = opts.addBtnId ? document.getElementById(opts.addBtnId) : null;
    const modalId = opts.modalId || 'quickVendorModal';
    let timer = null;

    if (!search || !idEl || !results) return;

    ensureSaveHandler(modalId);
    ensureModalLifecycle(modalId);

    function setSelected(id, name) {
      idEl.value = id || '';
      if (name) search.value = name;
      if (selected) {
        selected.textContent = name ? name : opts.required ? 'Select a vendor' : 'No vendor';
      }
    }

    search.addEventListener('input', () => {
      clearTimeout(timer);
      idEl.value = '';
      if (selected) selected.textContent = opts.required ? 'Select a vendor' : 'No vendor';
      const q = search.value.trim();
      if (!q) {
        results.classList.add('d-none');
        results.innerHTML = '';
        return;
      }
      timer = setTimeout(async () => {
        try {
          const res = await fetch('/api/vendors/lookup?q=' + encodeURIComponent(q));
          const data = await res.json();
          const rows = data.results || [];
          if (!rows.length) {
            results.innerHTML = `<button type="button" class="lookup-item lookup-create" data-name="${escapeHtml(q)}">+ Add "${escapeHtml(q)}"</button>`;
          } else {
            results.innerHTML =
              rows
                .map(
                  (v) =>
                    `<button type="button" class="lookup-item" data-id="${v.id}" data-name="${escapeHtml(v.name)}">${escapeHtml(v.label)}</button>`
                )
                .join('') +
              `<button type="button" class="lookup-item lookup-create" data-name="${escapeHtml(q)}">+ Add new vendor</button>`;
          }
          results.classList.remove('d-none');
        } catch (_) {
          results.classList.add('d-none');
        }
      }, 200);
    });

    results.addEventListener('click', (e) => {
      const btn = e.target.closest('.lookup-item');
      if (!btn) return;
      e.preventDefault();
      if (btn.classList.contains('lookup-create')) {
        results.classList.add('d-none');
        openQuickModal(modalId, btn.dataset.name || '', setSelected);
        return;
      }
      setSelected(btn.dataset.id, btn.dataset.name);
      results.classList.add('d-none');
    });

    addBtn?.addEventListener('click', () => {
      openQuickModal(modalId, search.value.trim(), setSelected);
    });

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.lookup-results') && !e.target.closest('#' + opts.searchId)) {
        results.classList.add('d-none');
      }
    });
  }

  function ensureSelected(idId, required) {
    const idEl = document.getElementById(idId);
    const id = (idEl?.value || '').trim();
    if (required && !id) {
      return { ok: false, error: 'Vendor is required. Search or add a vendor.' };
    }
    return { ok: true, id: id || null };
  }

  global.VendorLookup = { bind, ensureSelected };
})(window);
