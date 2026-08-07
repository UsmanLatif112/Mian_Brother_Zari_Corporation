/**
 * Quick-add expense category (+ next to category dropdown).
 */
(function () {
  const qecModal = document.getElementById('quickExpenseCategoryModal');
  if (!qecModal) return;

  const qecName = document.getElementById('qec-name');
  const qecError = document.getElementById('qec-error');
  const qecSave = document.getElementById('qec-save');
  let activeCategorySelect = null;

  const csrf = () => window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;

  function allCategorySelects() {
    return document.querySelectorAll('.expense-category-select');
  }

  function appendCategoryOption(id, name, selectEl) {
    allCategorySelects().forEach((sel) => {
      if (sel.querySelector(`option[value="${id}"]`)) return;
      const opt = document.createElement('option');
      opt.value = String(id);
      opt.textContent = name;
      sel.appendChild(opt);
    });
    if (selectEl) selectEl.value = String(id);
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-quick-expense-category');
    if (!btn) return;
    const cell = btn.closest('.expense-category-cell, .input-group');
    activeCategorySelect = cell?.querySelector('.expense-category-select') || null;
    if (qecName) qecName.value = '';
    if (qecError) {
      qecError.classList.add('d-none');
      qecError.textContent = '';
    }
    bootstrap.Modal.getOrCreateInstance(qecModal).show();
    setTimeout(() => qecName?.focus(), 200);
  });

  qecModal.addEventListener('hidden.bs.modal', () => {
    if (document.querySelector('.modal.show')) {
      document.body.classList.add('modal-open');
    }
  });

  qecSave?.addEventListener('click', async () => {
    const name = (qecName?.value || '').trim();
    if (!name) {
      if (qecError) {
        qecError.textContent = 'Enter a category name.';
        qecError.classList.remove('d-none');
      }
      return;
    }
    qecSave.disabled = true;
    try {
      const res = await fetch('/api/expense-categories/quick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!data.ok) {
        if (qecError) {
          qecError.textContent = data.error || 'Could not save category.';
          qecError.classList.remove('d-none');
        }
        return;
      }
      appendCategoryOption(data.id, data.name, activeCategorySelect);
      bootstrap.Modal.getInstance(qecModal)?.hide();
    } catch (_) {
      if (qecError) {
        qecError.textContent = 'Network error. Try again.';
        qecError.classList.remove('d-none');
      }
    } finally {
      qecSave.disabled = false;
    }
  });

  qecName?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      qecSave?.click();
    }
  });
})();
