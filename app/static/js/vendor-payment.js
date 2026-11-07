/**
 * Vendor Record Payment: always show current books amount —
 * Payable (we pay) OR To get from vendor — with live Paid/Received + Remain calc.
 */
(function () {
  function money(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return '0';
    if (Math.abs(v - Math.round(v)) < 0.001) return String(Math.round(v));
    return v.toFixed(2).replace(/\.?0+$/, '') || '0';
  }

  function csrf() {
    return window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content || '';
  }

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }

  function initVendorPaymentModal(root) {
    const form = root || document.getElementById('payment-form');
    if (!form || form.dataset.vendorPaymentUiReady === '1') return;
    form.dataset.vendorPaymentUiReady = '1';

    const vendorEl = qs('#pay-vendor', form) || qs('[name=vendor_id]', form);
    const amountEl = qs('#pay-amount', form) || qs('[name=amount]', form);
    const typeEl = qs('#pay-type', form) || qs('[name=payment_type]', form);
    const balanceEl = qs('#pay-balance-value', form);
    const balanceLabel = qs('#pay-balance-label', form);
    const paidLabel = qs('#pay-paid-label', form);
    const paidEl = qs('#pay-paid-display', form);
    const remainWrap = qs('#pay-remain-wrap', form);
    const remainEl = qs('#pay-remain-value', form);
    const remainLabel = qs('#pay-remain-label', form);
    const hintEl = qs('#pay-hint', form) || document.getElementById('pay-hint');
    const paymentModalEl = document.getElementById('paymentModal');

    let lastInfo = null;

    function currentAmount() {
      if (lastInfo && lastInfo.direction && lastInfo.current_amount != null) {
        const amount = Number(lastInfo.current_amount);
        const direction = lastInfo.direction;
        if (direction === 'payable' || direction === 'receive' || direction === 'settled') {
          return {
            amount: Number.isFinite(amount) ? Math.max(0, amount) : 0,
            direction,
          };
        }
      }
      const signed = Number(lastInfo?.balance);
      if (Number.isFinite(signed)) {
        if (signed > 0.001) return { amount: signed, direction: 'payable' };
        if (signed < -0.001) return { amount: Math.abs(signed), direction: 'receive' };
        return { amount: 0, direction: 'settled' };
      }
      const owes = Number(lastInfo?.owes ?? lastInfo?.payable ?? 0);
      const adv = Number(lastInfo?.advance ?? lastInfo?.prepaid ?? 0);
      if (owes > 0) return { amount: owes, direction: 'payable' };
      if (adv > 0) return { amount: adv, direction: 'receive' };
      return { amount: 0, direction: 'settled' };
    }

    function setCalc() {
      if (!balanceEl || !remainEl) return;
      const { amount, direction } = currentAmount();
      const paid = Number(amountEl?.value || 0);
      const safeBal = Number.isFinite(amount) ? Math.max(0, amount) : 0;
      const safePaid = Number.isFinite(paid) ? Math.max(0, paid) : 0;
      const ptype = (typeEl?.value || 'account_settle').toLowerCase();
      const isGet = direction === 'receive';

      if (balanceLabel) {
        if (direction === 'payable') balanceLabel.textContent = 'Payable (we pay vendor)';
        else if (direction === 'receive') balanceLabel.textContent = 'To get from vendor';
        else balanceLabel.textContent = 'Balance';
      }
      balanceEl.textContent = money(safeBal);
      balanceEl.classList.toggle('text-warning', direction === 'payable' && safeBal > 0);
      balanceEl.classList.toggle('text-success', isGet && safeBal > 0);

      if (paidLabel) paidLabel.textContent = isGet ? 'Received' : 'Paid';
      if (paidEl) paidEl.textContent = money(safePaid);

      if (hintEl) {
        if (isGet) {
          hintEl.textContent =
            'To get from vendor: use Loan when vendor gives cash back. Amount and remain update live.';
        } else if (direction === 'payable') {
          hintEl.textContent =
            'Payable: Account settle / Advance = we pay vendor. Loan = vendor gives cash (we owe more).';
        } else {
          hintEl.textContent =
            'Account settle: we pay against payable. Advance: pay ahead. Loan: vendor gives us cash.';
        }
      }

      if (!remainWrap) return;
      remainWrap.classList.remove('d-none');

      if (ptype === 'loan') {
        // Cash from vendor: reduces "to get", or increases payable.
        if (isGet) {
          if (remainLabel) remainLabel.textContent = 'Still to get';
          const left = Math.max(0, safeBal - safePaid);
          remainEl.textContent = money(left);
          if (safePaid > safeBal + 0.001) {
            if (remainLabel) remainLabel.textContent = 'Payable after';
            remainEl.textContent = money(safePaid - safeBal);
            remainEl.classList.remove('text-success');
            remainEl.classList.add('text-warning');
          } else {
            remainEl.classList.remove('text-warning');
            remainEl.classList.add('text-success');
          }
        } else {
          if (remainLabel) remainLabel.textContent = 'Payable after';
          remainEl.textContent = money(safeBal + safePaid);
          remainEl.classList.remove('text-success');
          remainEl.classList.add('text-warning');
        }
        return;
      }

      // Settle / advance = we pay vendor cash out
      if (isGet) {
        // Paying while already "to get" increases prepaid (more to get)
        if (remainLabel) remainLabel.textContent = 'To get after';
        remainEl.textContent = money(safeBal + safePaid);
        remainEl.classList.remove('text-warning');
        remainEl.classList.add('text-success');
        return;
      }

      if (safePaid > safeBal + 0.001) {
        if (remainLabel) remainLabel.textContent = 'Advance (to get)';
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

    async function loadPaymentInfo(vendorId) {
      lastInfo = null;
      if (balanceEl) {
        balanceEl.textContent = '—';
        balanceEl.classList.remove('text-warning', 'text-success');
      }
      if (balanceLabel) balanceLabel.textContent = 'Balance';
      if (paidLabel) paidLabel.textContent = 'Paid';
      if (paidEl) paidEl.textContent = '0';
      if (remainEl) remainEl.textContent = '—';
      if (!vendorId) return;
      try {
        const res = await fetch(`/api/vendors/${vendorId}/payment-info`, {
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
          window.showErpToast('warning', 'Could not load vendor balance.');
        }
      }
    }

    vendorEl?.addEventListener('change', () => {
      loadPaymentInfo(vendorEl.value);
    });
    amountEl?.addEventListener('input', setCalc);
    typeEl?.addEventListener('change', setCalc);

    // Click amount row to fill full current balance into the amount field
    balanceEl?.addEventListener('click', () => {
      const { amount } = currentAmount();
      if (!amountEl || !(amount > 0)) return;
      amountEl.value = String(amount);
      setCalc();
    });

    paymentModalEl?.addEventListener('shown.bs.modal', () => {
      const vid = vendorEl?.value;
      if (vid) loadPaymentInfo(vid);
      else setCalc();
    });

    if (vendorEl?.value) {
      loadPaymentInfo(vendorEl.value);
    }

    window.reloadVendorPaymentInfo = function (vendorId) {
      if (vendorId != null && vendorEl && String(vendorEl.value) !== String(vendorId)) {
        vendorEl.value = String(vendorId);
      }
      loadPaymentInfo(vendorEl?.value);
    };
  }

  function boot() {
    initVendorPaymentModal(document.getElementById('payment-form'));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.initVendorPaymentModal = initVendorPaymentModal;
})();
