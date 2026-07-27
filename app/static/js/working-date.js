(function () {
  const STORAGE_KEY = 'erp-working-date';

  function readBodyDate() {
    return document.body.dataset.workingDate || '';
  }

  function calendarToday() {
    return document.body.dataset.calendarToday || new Date().toISOString().slice(0, 10);
  }

  function setWorkingDate(value) {
    if (!value) return;
    window.ERP_WORKING_DATE = value;
    document.body.dataset.workingDate = value;
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch (_) { /* ignore */ }
    applyWorkingDateToForms();
    document.dispatchEvent(new CustomEvent('erp:working-date-changed', { detail: { date: value } }));
  }

  function getErpWorkingDate() {
    return window.ERP_WORKING_DATE || readBodyDate() || calendarToday();
  }

  function isFilterDateInput(input) {
    if (!input || input.type !== 'date') return true;
    if (input.closest('.custom-date-field')) return true;
    if (input.closest('form[data-persist-filters]')) return true;
    if (input.closest('.working-date-panel')) return true;
    if (input.id === 'working-date-input') return true;
    if (input.hasAttribute('data-erp-no-working-date')) return true;
    if (input.name === 'start' || input.name === 'end') return true;
    if (input.name === 'start_date' || input.name === 'end_date') return true;
    return false;
  }

  function shouldApplyWorkingDate(input) {
    if (!input || input.type !== 'date') return false;
    if (isFilterDateInput(input)) return false;
    if (input.classList.contains('erp-entry-date')) return true;
    if (input.hasAttribute('data-erp-entry-date')) return true;
    const name = (input.name || input.id || '').toLowerCase();
    const entryNames = [
      'sale_date',
      'purchase_date',
      'expense_date',
      'payment_date',
      'taken_date',
      'entry_date',
      'balance_date',
      'receiving_date',
      'joined_date',
    ];
    return entryNames.some((part) => name.includes(part));
  }

  function applyWorkingDateToForms(root) {
    const scope = root || document;
    const value = getErpWorkingDate();
    scope.querySelectorAll('input[type="date"]').forEach((input) => {
      if (!shouldApplyWorkingDate(input)) return;
      if (input.dataset.erpPreserveDate === '1' && input.value) return;
      input.value = value;
      if (input.dataset) input.dataset.today = value;
    });
  }

  function formatDisplayDate(iso) {
    if (!iso) return '';
    const parts = iso.split('-');
    if (parts.length !== 3) return iso;
    const dt = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    if (Number.isNaN(dt.getTime())) return iso;
    return dt.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
  }

  function updateWorkingDateToggle(iso) {
    const label = document.getElementById('working-date-toggle-label');
    const toggle = document.querySelector('.working-date-toggle');
    if (label && iso) label.textContent = formatDisplayDate(iso);
    if (toggle) {
      const custom = iso && iso !== calendarToday();
      toggle.classList.toggle('is-custom', custom);
    }
  }

  function closeWorkingDateDropdown() {
    const toggle = document.querySelector('.working-date-toggle');
    if (!toggle || !window.bootstrap) return;
    bootstrap.Dropdown.getInstance(toggle)?.hide();
  }

  async function postWorkingDate(body) {
    const res = await fetch('/api/working-date', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content || '',
      },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Could not update working date.');
    }
    return data;
  }

  function initWorkingDateControl() {
    const panel = document.getElementById('working-date-control');
    if (!panel) return;

    const initial = readBodyDate() || calendarToday();
    setWorkingDate(initial);
    updateWorkingDateToggle(initial);

    const input = document.getElementById('working-date-input');

    async function applyFromControl() {
      const value = (input?.value || '').trim();
      if (!value) return;
      try {
        const data = await postWorkingDate({ date: value });
        setWorkingDate(data.working_date || value);
        updateWorkingDateToggle(data.working_date || value);
        closeWorkingDateDropdown();
        if (window.showErpToast) {
          showErpToast('success', data.message || 'Working date updated.');
        }
        window.location.reload();
      } catch (err) {
        if (window.showErpToast) showErpToast('danger', err.message || 'Could not update working date.');
      }
    }

    async function resetToToday() {
      try {
        const data = await postWorkingDate({ clear: true });
        setWorkingDate(data.working_date || calendarToday());
        updateWorkingDateToggle(data.working_date || calendarToday());
        closeWorkingDateDropdown();
        if (window.showErpToast) {
          showErpToast('success', data.message || 'Working date reset.');
        }
        window.location.reload();
      } catch (err) {
        if (window.showErpToast) showErpToast('danger', err.message || 'Could not reset working date.');
      }
    }

    // Use delegation so clicks work reliably inside Bootstrap dropdown.
    panel.addEventListener('click', (e) => {
      const applyBtn = e.target.closest('#working-date-apply');
      const resetBtn = e.target.closest('#working-date-reset');
      if (applyBtn) {
        e.preventDefault();
        e.stopPropagation();
        applyFromControl();
      } else if (resetBtn) {
        e.preventDefault();
        e.stopPropagation();
        resetToToday();
      }
    });

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        applyFromControl();
      }
    });

    document.querySelectorAll('.modal').forEach((modal) => {
      modal.addEventListener('shown.bs.modal', () => applyWorkingDateToForms(modal));
    });
  }

  window.getErpWorkingDate = getErpWorkingDate;
  window.applyErpWorkingDate = applyWorkingDateToForms;

  if (document.body.classList.contains('erp-body')) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initWorkingDateControl);
    } else {
      initWorkingDateControl();
    }
  }
})();
