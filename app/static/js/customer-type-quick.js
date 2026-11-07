/**
 * Quick-add customer type (+ next to type dropdown).
 */
(function () {
  const qctModal = document.getElementById('quickCustomerTypeModal');
  if (!qctModal) return;

  const qctName = document.getElementById('qct-name');
  const qctDue = document.getElementById('qct-due');
  const qctError = document.getElementById('qct-error');
  const qctSave = document.getElementById('qct-save');
  let activeSelect = null;

  const csrf = () =>
    window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;

  // Keep parent Add/Edit Customer modal usable after this dialog closes.
  window.ErpModalNesting?.bindQuickModal?.('quickCustomerTypeModal');

  function allTypeSelects() {
    return document.querySelectorAll('.customer-type-select');
  }

  function appendTypeOption(code, name, selectEl) {
    allTypeSelects().forEach((sel) => {
      const exists = Array.from(sel.options).some((o) => o.value === code);
      if (exists) return;
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    if (selectEl) selectEl.value = code;
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-quick-customer-type');
    if (!btn) return;
    e.preventDefault();
    const cell = btn.closest('.customer-type-cell, .input-group');
    activeSelect = cell?.querySelector('.customer-type-select') || null;
    if (qctName) qctName.value = '';
    if (qctDue) qctDue.value = '30';
    if (qctError) {
      qctError.classList.add('d-none');
      qctError.textContent = '';
    }
    bootstrap.Modal.getOrCreateInstance(qctModal).show();
    setTimeout(() => qctName?.focus(), 200);
  });

  qctSave?.addEventListener('click', async () => {
    const name = (qctName?.value || '').trim();
    if (!name) {
      if (qctError) {
        qctError.textContent = 'Enter a type name.';
        qctError.classList.remove('d-none');
      }
      return;
    }
    qctSave.disabled = true;
    try {
      const res = await fetch('/api/customer-types/quick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({
          name,
          due_days: Number(qctDue?.value || 30),
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        if (qctError) {
          qctError.textContent = data.error || 'Could not save type.';
          qctError.classList.remove('d-none');
        }
        return;
      }
      appendTypeOption(data.code, data.name, activeSelect);
      bootstrap.Modal.getInstance(qctModal)?.hide();
    } catch (_) {
      if (qctError) {
        qctError.textContent = 'Network error. Try again.';
        qctError.classList.remove('d-none');
      }
    } finally {
      qctSave.disabled = false;
    }
  });

  qctName?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      qctSave?.click();
    }
  });
})();
