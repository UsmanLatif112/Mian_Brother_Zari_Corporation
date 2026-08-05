(function () {
  const STORAGE_KEY = 'erp-working-date';

  function readBodyDate() {
    return document.body.dataset.workingDate || '';
  }

  function calendarToday() {
    return document.body.dataset.calendarToday || localIsoToday();
  }

  function localIsoToday() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
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
    const raw = (input.name || input.id || '').toLowerCase();
    if (raw.includes('expiry') || raw.includes('expire') || raw.includes('due_date')) {
      return false;
    }
    if (input.classList.contains('erp-entry-date')) return true;
    if (input.hasAttribute('data-erp-entry-date')) return true;
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
      'settled',
    ];
    return entryNames.some((part) => raw.includes(part));
  }

  function applyWorkingDateToForms(root, { force = false } = {}) {
    const scope = root || document;
    const value = getErpWorkingDate();
    scope.querySelectorAll('input[type="date"]').forEach((input) => {
      if (!shouldApplyWorkingDate(input)) return;
      if (!force && input.value) return;
      if (!force && input.dataset.erpPreserveDate === '1' && input.value) return;
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
    const toggle = document.getElementById('working-date-toggle')
      || document.querySelector('.working-date-toggle');
    if (label && iso) label.textContent = formatDisplayDate(iso);
    if (toggle) {
      const custom = iso && iso !== calendarToday();
      toggle.classList.toggle('is-custom', custom);
    }
  }

  function setPanelOpen(open) {
    const toggle = document.getElementById('working-date-toggle');
    const menu = document.getElementById('working-date-menu');
    if (!menu) return;
    menu.hidden = !open;
    menu.classList.toggle('is-open', open);
    if (toggle) {
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.classList.toggle('show', open);
    }
  }

  function closeWorkingDatePanel() {
    setPanelOpen(false);
  }

  async function postWorkingDate(body) {
    const res = await fetch('/api/working-date', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-CSRFToken': window.CSRF_TOKEN
          || document.querySelector('meta[name=csrf-token]')?.content
          || '',
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

  function bindWorkingDateControl(root) {
    if (!root || root.dataset.erpWdBound === '1') return;
    root.dataset.erpWdBound = '1';

    const toggle = document.getElementById('working-date-toggle');
    const menu = document.getElementById('working-date-menu');
    const input = document.getElementById('working-date-input');
    const applyBtn = document.getElementById('working-date-apply');
    const resetBtn = document.getElementById('working-date-reset');

    async function applyFromControl() {
      const value = (input?.value || '').trim();
      if (!value) {
        if (window.showErpToast) showErpToast('warning', 'Please select a date first.');
        return;
      }
      if (applyBtn) applyBtn.disabled = true;
      try {
        const data = await postWorkingDate({ date: value });
        setWorkingDate(data.working_date || value);
        updateWorkingDateToggle(data.working_date || value);
        closeWorkingDatePanel();
        if (window.showErpToast) {
          showErpToast('success', data.message || 'Working date updated.');
        }
        window.location.reload();
      } catch (err) {
        if (window.showErpToast) showErpToast('danger', err.message || 'Could not update working date.');
        if (applyBtn) applyBtn.disabled = false;
      }
    }

    async function resetToToday() {
      if (resetBtn) resetBtn.disabled = true;
      try {
        const data = await postWorkingDate({ clear: true });
        setWorkingDate(data.working_date || calendarToday());
        updateWorkingDateToggle(data.working_date || calendarToday());
        closeWorkingDatePanel();
        if (window.showErpToast) {
          showErpToast('success', data.message || 'Working date reset.');
        }
        window.location.reload();
      } catch (err) {
        if (window.showErpToast) showErpToast('danger', err.message || 'Could not reset working date.');
        if (resetBtn) resetBtn.disabled = false;
      }
    }

    // Toggle open/close (custom panel — not Bootstrap dropdown)
    toggle?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      setPanelOpen(!!menu?.hidden);
    });

    // Direct handlers so Apply is always clickable (Bootstrap no longer intercepts)
    applyBtn?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      applyFromControl();
    });
    resetBtn?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      resetToToday();
    });

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        applyFromControl();
      }
    });
    // Keep panel open while interacting with the native date picker
    input?.addEventListener('mousedown', (e) => e.stopPropagation());
    input?.addEventListener('click', (e) => e.stopPropagation());
    menu?.addEventListener('mousedown', (e) => e.stopPropagation());
    menu?.addEventListener('click', (e) => e.stopPropagation());

    document.addEventListener('click', (e) => {
      if (!root.contains(e.target)) closeWorkingDatePanel();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeWorkingDatePanel();
    });
  }

  function initWorkingDate() {
    const initial = readBodyDate() || calendarToday();
    setWorkingDate(initial);
    updateWorkingDateToggle(initial);
    bindWorkingDateControl(document.getElementById('working-date-control'));

    document.querySelectorAll('.modal').forEach((modal) => {
      modal.addEventListener('shown.bs.modal', () => {
        applyWorkingDateToForms(modal);
      });
    });
  }

  window.getErpWorkingDate = getErpWorkingDate;
  window.applyErpWorkingDate = applyWorkingDateToForms;

  if (document.body.classList.contains('erp-body')) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initWorkingDate);
    } else {
      initWorkingDate();
    }
  }
})();
