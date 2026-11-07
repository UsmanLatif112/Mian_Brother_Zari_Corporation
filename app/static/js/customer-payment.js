/**
 * Record Payment: live balance / remain / advance + unpaid dues picker modal.
 */
(function () {
  function money(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return '0';
    // Whole rupees: 12000 — keep decimals only when needed: 12000.5
    if (Math.abs(v - Math.round(v)) < 0.001) return String(Math.round(v));
    return v.toFixed(2).replace(/\.?0+$/, '') || '0';
  }

  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function csrf() {
    return window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content || '';
  }

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }

  function initCustomerPaymentModal(root) {
    const form = root || document.getElementById('payment-form');
    if (!form || form.dataset.paymentUiReady === '1') return;
    form.dataset.paymentUiReady = '1';

    const customerEl = qs('#pay-customer', form) || qs('[name=customer_id]', form);
    const amountEl = qs('#pay-amount', form) || qs('[name=amount]', form);
    const remarksEl = qs('#pay-remarks', form) || qs('[name=remarks]', form);
    const typeEl = qs('#pay-type', form) || qs('[name=payment_type]', form);
    const dueWrap = qs('#pay-due-wrap', form);
    const dueEnabled = qs('#pay-due-enabled', form);
    const dueEnabledValue = qs('#pay-due-enabled-value', form);
    const dueDateEl = qs('#pay-due-date', form);
    const balanceEl = qs('#pay-balance-value', form);
    const paidEl = qs('#pay-paid-display', form);
    const remainWrap = qs('#pay-remain-wrap', form);
    const remainEl = qs('#pay-remain-value', form);
    const remainLabel = qs('#pay-remain-label', form);
    const openUnpaidBtn = qs('#btn-open-unpaid-dues', form);
    const paymentModalEl = document.getElementById('paymentModal');
    const unpaidModalEl = document.getElementById('unpaidDuesModal');

    let lastInfo = null;

    function syncLoanDueUI() {
      if (!dueWrap || !dueEnabled || !dueDateEl) return;
      const isLoan = (typeEl?.value || '') === 'loan';
      dueWrap.classList.toggle('d-none', !isLoan);
      if (!isLoan) {
        dueEnabled.checked = false;
        dueDateEl.value = '';
        dueDateEl.disabled = true;
        if (dueEnabledValue) dueEnabledValue.value = '0';
        return;
      }
      dueDateEl.disabled = !dueEnabled.checked;
      if (!dueEnabled.checked) dueDateEl.value = '';
      if (dueEnabledValue) dueEnabledValue.value = dueEnabled.checked ? '1' : '0';
    }

    async function suggestLoanDueDate() {
      if (!dueEnabled?.checked || !dueDateEl || dueDateEl.value) return;
      const customerId = customerEl?.value;
      if (!customerId) return;
      try {
        const base = qs('#pay-date', form)?.value || '';
        const res = await fetch(
          `/api/customers/${customerId}/suggest-due-date?base=${encodeURIComponent(base)}`,
          { credentials: 'same-origin' }
        );
        const data = await res.json();
        if (data.ok && data.due_date) dueDateEl.value = data.due_date;
      } catch (_) { /* ignore */ }
    }

    typeEl?.addEventListener('change', syncLoanDueUI);
    dueEnabled?.addEventListener('change', () => {
      syncLoanDueUI();
      if (dueEnabled.checked) suggestLoanDueDate();
    });
    syncLoanDueUI();

    function setCalc() {
      if (!balanceEl || !remainEl) return;
      const bal = Number(lastInfo?.owes ?? lastInfo?.balance ?? 0);
      const paid = Number(amountEl?.value || 0);
      const safeBal = Number.isFinite(bal) ? Math.max(0, bal) : 0;
      const safePaid = Number.isFinite(paid) ? Math.max(0, paid) : 0;
      balanceEl.textContent = money(safeBal);
      if (paidEl) paidEl.textContent = money(safePaid);
      if (!remainWrap) return;
      remainWrap.classList.remove('d-none');
      if (safePaid > safeBal + 0.001) {
        if (remainLabel) remainLabel.textContent = 'Advance';
        remainEl.textContent = money(safePaid - safeBal);
        remainEl.classList.remove('text-warning');
        remainEl.classList.add('text-success');
      } else {
        if (remainLabel) remainLabel.textContent = 'Remain';
        remainEl.textContent = money(Math.max(0, safeBal - safePaid));
        remainEl.classList.remove('text-success');
        remainEl.classList.add('text-warning');
      }
    }

    async function loadPaymentInfo(customerId) {
      lastInfo = null;
      if (balanceEl) balanceEl.textContent = '—';
      if (paidEl) paidEl.textContent = '0.00';
      if (remainEl) remainEl.textContent = '—';
      if (!customerId) {
        if (openUnpaidBtn) openUnpaidBtn.disabled = true;
        return;
      }
      if (openUnpaidBtn) openUnpaidBtn.disabled = false;
      try {
        const res = await fetch(`/api/customers/${customerId}/payment-info`, {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'fetch', 'X-CSRFToken': csrf() },
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'Could not load balance');
        lastInfo = data;
        setCalc();
      } catch (_) {
        if (balanceEl) balanceEl.textContent = '—';
        if (window.showErpToast) {
          window.showErpToast('warning', 'Could not load customer balance.');
        }
      }
    }

    function renderUnpaidList(info) {
      const body = document.getElementById('unpaid-dues-body');
      const totalEl = document.getElementById('unpaid-dues-total');
      const nameEl = document.getElementById('unpaid-dues-customer');
      if (!body) return;
      if (nameEl) nameEl.textContent = info.customer_name || '';
      const entries = info.entries || [];
      if (!entries.length) {
        body.innerHTML =
          '<tr><td colspan="5" class="text-muted text-center py-3">No unpaid sales or loans.</td></tr>';
      } else {
        body.innerHTML = entries
          .map((e) => {
            const amt = money(e.amount);
            const label = escapeHtml(e.label || '');
            const particulars = escapeHtml(e.particulars || '');
            const remark = escapeHtml(e.remark || e.particulars || e.label || '');
            const status = escapeHtml(e.status || '');
            const kind = escapeHtml(e.kind || '');
            const id = escapeHtml(e.id || '');
            const tip = escapeHtml(e.remark || `${e.label || ''} — ${e.particulars || ''}`);
            const rawAmt = Number(e.amount);
            const amountAttr = Number.isFinite(rawAmt) ? String(rawAmt) : '0';
            return `<tr>
              <td class="text-center">
                <input type="checkbox" class="form-check-input unpaid-due-check"
                       data-id="${id}" data-kind="${kind}"
                       data-label="${label}" data-amount="${amountAttr}"
                       data-status="${status}"
                       data-remark="${remark}"
                       data-particulars="${particulars}">
              </td>
              <td class="small">${escapeHtml(e.date || '—')}</td>
              <td class="journal-particulars" title="${tip}">
                <div class="fw-semibold small text-truncate" title="${label}">${label || '—'}</div>
                <span class="journal-particulars-text text-muted" title="${tip}">${particulars || '—'}</span>
              </td>
              <td><span class="badge bg-light text-dark border">${status || '—'}</span></td>
              <td class="text-end fw-semibold">${amt}</td>
            </tr>`;
          })
          .join('');
      }
      if (totalEl) totalEl.textContent = money(info.owes ?? 0);
      const selectedEl = document.getElementById('unpaid-dues-selected-total');
      if (selectedEl) selectedEl.textContent = '0';
      const selectAll = document.getElementById('unpaid-dues-select-all');
      if (selectAll) selectAll.checked = false;
    }

    function openUnpaidModal() {
      if (!lastInfo) {
        if (window.showErpToast) {
          window.showErpToast('warning', 'Select a customer first.');
        }
        return;
      }
      renderUnpaidList(lastInfo);
      if (!unpaidModalEl || !window.bootstrap) return;
      const unpaid = bootstrap.Modal.getOrCreateInstance(unpaidModalEl, {
        backdrop: true,
        focus: true,
      });
      unpaid.show();
      // Keep payment modal open; stack unpaid above it.
      setTimeout(() => {
        const backs = document.querySelectorAll('.modal-backdrop');
        backs.forEach((b, i) => {
          b.style.zIndex = String(1050 + i * 20);
        });
        if (paymentModalEl) paymentModalEl.style.zIndex = '1055';
        unpaidModalEl.style.zIndex = '1070';
      }, 10);
    }

    function selectedDueChecks() {
      return Array.from(document.querySelectorAll('.unpaid-due-check:checked'));
    }

    function selectedDueTotal() {
      return selectedDueChecks().reduce((sum, el) => {
        const n = Number(el.dataset.amount || 0);
        return sum + (Number.isFinite(n) ? n : 0);
      }, 0);
    }

    /** Push selected dues into Amount and refresh Balance / Paid / Remain. */
    function syncAmountFromSelection() {
      const total = selectedDueTotal();
      if (amountEl) {
        amountEl.value = total > 0 ? money(total) : '';
      }
      setCalc();
      const selectedEl = document.getElementById('unpaid-dues-selected-total');
      if (selectedEl) selectedEl.textContent = money(total);
    }

    function applySelectedToRemarks() {
      const checks = selectedDueChecks();
      if (!checks.length) {
        if (window.showErpToast) {
          window.showErpToast('warning', 'Select at least one unpaid entry.');
        }
        return;
      }
      syncAmountFromSelection();
      const lines = checks.map((el) => {
        const amount = money(el.dataset.amount || 0);
        const remark = (el.dataset.remark || el.dataset.label || '').trim();
        return `${remark} — ${amount}`;
      });
      const block = 'Against: ' + lines.join('; ');
      if (remarksEl) {
        const prev = (remarksEl.value || '').trim();
        // Replace a previous "Against:" block from this picker, keep other notes.
        const withoutAgainst = prev
          .split('\n')
          .filter((line) => !line.trim().startsWith('Against:'))
          .join('\n')
          .trim();
        remarksEl.value = withoutAgainst ? withoutAgainst + '\n' + block : block;
      }
      bootstrap.Modal.getInstance(unpaidModalEl)?.hide();
      // Restore focus to payment modal after nested close.
      setTimeout(() => {
        if (paymentModalEl) {
          paymentModalEl.style.zIndex = '';
          paymentModalEl.focus();
        }
        setCalc();
      }, 200);
    }

    customerEl?.addEventListener('change', () => {
      loadPaymentInfo(customerEl.value);
    });
    amountEl?.addEventListener('input', setCalc);
    openUnpaidBtn?.addEventListener('click', (e) => {
      e.preventDefault();
      openUnpaidModal();
    });
    document.getElementById('btn-apply-unpaid-dues')?.addEventListener('click', applySelectedToRemarks);
    document.getElementById('unpaid-dues-select-all')?.addEventListener('change', (e) => {
      const on = !!e.target.checked;
      document.querySelectorAll('.unpaid-due-check').forEach((c) => {
        c.checked = on;
      });
      syncAmountFromSelection();
    });
    unpaidModalEl?.addEventListener('change', (e) => {
      if (e.target && e.target.classList && e.target.classList.contains('unpaid-due-check')) {
        syncAmountFromSelection();
      }
    });

    unpaidModalEl?.addEventListener('hidden.bs.modal', () => {
      // Nested modal close can drop body.modal-open while payment is still open.
      if (paymentModalEl?.classList.contains('show')) {
        document.body.classList.add('modal-open');
        paymentModalEl.style.zIndex = '';
      }
    });

    paymentModalEl?.addEventListener('shown.bs.modal', () => {
      const cid = customerEl?.value;
      if (cid) loadPaymentInfo(cid);
      else setCalc();
      syncLoanDueUI();
    });

    form.addEventListener('submit', (e) => {
      syncLoanDueUI();
      if ((typeEl?.value || '') === 'loan' && dueEnabled?.checked && !dueDateEl?.value) {
        e.preventDefault();
        if (window.showErpToast) {
          window.showErpToast('warning', 'Enter a loan due date, or turn off the due date switch.');
        }
      }
    });

    // Preselected customer (ledger page / row button)
    if (customerEl?.value) {
      loadPaymentInfo(customerEl.value);
    }
  }

  function boot() {
    initCustomerPaymentModal(document.getElementById('payment-form'));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.initCustomerPaymentModal = initCustomerPaymentModal;
})();
