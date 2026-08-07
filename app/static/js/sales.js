(function () {
  const csrf = () => window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;
  const money = (n) => Number(n || 0).toFixed(2);
  const tbody = document.querySelector('#sale-lines tbody');
  let activeRow = null;
  let customerTimer = null;
  let productTimer = null;
  let editingSaleId = null;

  function headers() {
    return {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf(),
    };
  }

  function setProductThumb(tr, url) {
    const preview = tr.querySelector('.product-line-thumb .line-photo-preview');
    const wrap = tr.querySelector('.product-line-thumb');
    if (!preview || !wrap) return;
    if (url) {
      preview.style.backgroundImage = `url('${url}')`;
      preview.classList.add('has-photo');
      wrap.classList.add('has-photo');
    } else {
      preview.style.backgroundImage = '';
      preview.classList.remove('has-photo');
      wrap.classList.remove('has-photo');
    }
  }

  function rowTemplate() {
    const tr = document.createElement('tr');
    tr.classList.add('sale-line-row', 'mode-qty');
    tr.innerHTML = `
      <td class="line-photo-td">
        <div class="product-line-thumb" title="Product photo from inventory">
          <div class="line-photo-preview"><i class="fa-solid fa-box"></i></div>
        </div>
      </td>
      <td>
        <div class="position-relative">
          <div class="input-group input-group-sm erp-input-group-quick sale-item-search">
            <input type="text" class="form-control product-search" placeholder="Search item..." autocomplete="off">
            <button type="button" class="btn erp-btn-quick-add btn-quick-product" title="Add product"><i class="fa-solid fa-plus"></i></button>
          </div>
          <input type="hidden" class="product-id" value="">
          <input type="hidden" class="product-unit-weight" value="">
          <input type="hidden" class="product-weight-unit" value="">
          <div class="lookup-results d-none product-results"></div>
        </div>
      </td>
      <td class="sale-unit-wt-td">
        <div class="unit-wt-group input-group input-group-sm">
          <input type="number" min="0" step="0.001" class="form-control unit-weight bg-light" value="" readonly tabindex="-1" placeholder="—">
          <span class="input-group-text unit-weight-suffix bg-light px-1 small">—</span>
        </div>
      </td>
      <td class="sale-qty-wt-cell">
        <div class="sale-qty-wt-wrap">
          <select class="form-select form-select-sm sale-mode d-none" title="Full bag or open by weight" aria-label="Sale mode">
            <option value="full" selected>Full</option>
            <option value="open">Open</option>
          </select>
          <div class="sale-qty-wt-inputs">
            <div class="input-group input-group-sm sale-weight-group d-none">
              <input type="number" min="0.001" step="0.001" class="form-control sale-weight" value="" title="Sale weight">
              <span class="input-group-text sale-weight-suffix bg-light px-1 small">kg</span>
            </div>
            <input type="number" min="0.001" step="1" class="form-control form-control-sm qty" value="1" title="Quantity (full units)">
          </div>
        </div>
      </td>
      <td>
        <input type="number" min="0" step="0.01" class="form-control form-control-sm list-price bg-light" value="0" readonly tabindex="-1" title="Default full-unit sale price">
      </td>
      <td>
        <input type="number" min="0" step="0.01" class="form-control form-control-sm price" value="0" title="Editable for this sale only">
      </td>
      <td class="text-end line-total fw-semibold">0.00</td>
      <td class="text-end">
        <button type="button" class="btn btn-sm btn-light border-0 sale-row-remove btn-remove-row" title="Remove row">
          <i class="fa-solid fa-trash-can text-danger"></i>
        </button>
      </td>
    `;
    return tr;
  }

  function rowUnitWeight(tr) {
    return Number(tr.querySelector('.product-unit-weight')?.value || 0);
  }

  function hasUnitWeight(tr) {
    return rowUnitWeight(tr) > 0;
  }

  function getSaleMode(tr) {
    if (!hasUnitWeight(tr)) return 'qty';
    return tr.querySelector('.sale-mode')?.value === 'open' ? 'open' : 'full';
  }

  function setSaleMode(tr, mode) {
    const sel = tr.querySelector('.sale-mode');
    if (sel && (mode === 'full' || mode === 'open')) sel.value = mode;
  }

  /** Open = sell by weight. Full / plain qty = quantity field. */
  function isOpenSale(tr) {
    return getSaleMode(tr) === 'open';
  }

  function suggestedWeightAmount(tr) {
    const uw = rowUnitWeight(tr);
    const sw = Number(tr.querySelector('.sale-weight')?.value || 0);
    const list = Number(tr.querySelector('.list-price')?.value || 0);
    if (!(uw > 0) || !(sw > 0)) return list;
    return list * (sw / uw);
  }

  function applyRowMode(tr) {
    const weighted = hasUnitWeight(tr);
    const open = isOpenSale(tr);
    const modeSel = tr.querySelector('.sale-mode');
    const weightGroup = tr.querySelector('.sale-weight-group');
    const qty = tr.querySelector('.qty');
    const suffix = tr.querySelector('.unit-weight-suffix');
    const wSuffix = tr.querySelector('.sale-weight-suffix');
    const unit = tr.querySelector('.product-weight-unit')?.value || 'kg';
    const unitInput = tr.querySelector('.unit-weight');

    tr.classList.toggle('mode-weight', open);
    tr.classList.toggle('mode-qty', !open);
    tr.classList.toggle('has-unit-weight', weighted);

    if (modeSel) modeSel.classList.toggle('d-none', !weighted);
    if (weightGroup) weightGroup.classList.toggle('d-none', !open);
    if (qty) {
      qty.classList.toggle('d-none', open);
      qty.step = weighted && !open ? '1' : '0.001';
      qty.title = open ? 'Quantity (from weight)' : weighted ? 'Full units' : 'Quantity (pieces)';
    }
    if (suffix) suffix.textContent = weighted ? unit : '—';
    if (unitInput && weighted) {
      unitInput.placeholder = '—';
      unitInput.title = 'Fixed bag weight for this product';
    }
    if (wSuffix) wSuffix.textContent = unit;
  }

  function inferEditMode(p) {
    const uw = Number(p.unit_weight || 0);
    if (!(uw > 0)) return 'qty';
    if (p.sale_mode === 'open' || p.sale_mode === 'full') return p.sale_mode;
    const qty = Number(p.quantity || 0);
    const sw = p.sale_weight != null && p.sale_weight !== '' ? Number(p.sale_weight) : null;
    if (sw != null && sw > 0) {
      const whole = Math.abs(qty - Math.round(qty)) < 0.001;
      const matchesFull = Math.abs(sw - qty * uw) < 0.001;
      if (whole && matchesFull) return 'full';
      return 'open';
    }
    return 'full';
  }

  function fillProductOnRow(tr, p, { keepAmount = false } = {}) {
    const unitWeight = Number(p.unit_weight || 0);
    const weightUnit = p.weight_unit || '';
    const listPrice = Number(
      p.list_price ?? p.list_unit_price ?? p.sale_price ?? p.unit_price ?? 0
    );
    tr.querySelector('.product-id').value = p.id || p.product_id || '';
    tr.querySelector('.product-search').value = p.name || '';
    tr.querySelector('.product-unit-weight').value = unitWeight > 0 ? unitWeight : '';
    tr.querySelector('.product-weight-unit').value = weightUnit;
    tr.querySelector('.unit-weight').value = unitWeight > 0 ? unitWeight : '';
    tr.querySelector('.list-price').value = money(listPrice);

    const mode = unitWeight > 0 ? inferEditMode({ ...p, unit_weight: unitWeight }) : 'qty';
    if (unitWeight > 0) setSaleMode(tr, mode === 'open' ? 'open' : 'full');
    applyRowMode(tr);

    if (unitWeight > 0 && mode === 'open') {
      const saleW =
        p.sale_weight != null && p.sale_weight !== ''
          ? Number(p.sale_weight)
          : unitWeight;
      tr.querySelector('.sale-weight').value = saleW;
      tr.querySelector('.qty').value = saleW / unitWeight;
      if (keepAmount && p.line_total != null) {
        tr.querySelector('.price').value = money(p.line_total);
        tr.dataset.priceDirty = '1';
      } else {
        tr.querySelector('.price').value = money(listPrice * (saleW / unitWeight));
        tr.dataset.priceDirty = '0';
      }
    } else {
      // Full units or plain piece sale
      if (p.quantity != null) tr.querySelector('.qty').value = p.quantity;
      else if (!tr.querySelector('.qty').value) tr.querySelector('.qty').value = 1;
      if (unitWeight > 0) {
        const q = Number(tr.querySelector('.qty').value || 0);
        tr.querySelector('.sale-weight').value = q > 0 ? q * unitWeight : unitWeight;
      } else {
        tr.querySelector('.sale-weight').value = '';
      }
      const perUnit =
        keepAmount && p.unit_price != null && !Number.isNaN(Number(p.unit_price)) && mode !== 'open'
          ? Number(p.unit_price)
          : listPrice;
      // On edit of full/piece: prefer list for dirty reset; keep line via unit_price when keepAmount
      if (keepAmount && p.line_total != null && unitWeight > 0 && mode === 'full') {
        const q = Number(tr.querySelector('.qty').value || 0);
        tr.querySelector('.price').value = money(q > 0 ? Number(p.line_total) / q : listPrice);
        tr.dataset.priceDirty = '1';
      } else if (keepAmount && p.unit_price != null && !(unitWeight > 0)) {
        tr.querySelector('.price').value = money(perUnit);
        tr.dataset.priceDirty = '1';
      } else {
        tr.querySelector('.price').value = money(listPrice);
        tr.dataset.priceDirty = '0';
      }
    }
    setProductThumb(tr, p.photo_url || null);
  }

  function lineAmount(tr) {
    if (isOpenSale(tr)) {
      return Number(tr.querySelector('.price')?.value || 0);
    }
    const qty = Number(tr.querySelector('.qty')?.value || 0);
    const price = Number(tr.querySelector('.price')?.value || 0);
    return qty * price;
  }

  function subtotal() {
    let sum = 0;
    tbody.querySelectorAll('tr').forEach((tr) => {
      sum += lineAmount(tr);
    });
    return sum;
  }

  function discountAmount() {
    const disc = Number(document.getElementById('sale-discount')?.value || 0);
    return Math.max(0, disc);
  }

  function grandTotal() {
    return Math.max(0, subtotal() - discountAmount());
  }

  function updateCustomerRequirement() {
    const status = document.getElementById('payment-status')?.value || 'paid';
    const hint = document.getElementById('customer-optional-hint');
    if (!hint) return;
    if (status === 'paid') {
      hint.textContent = '(optional)';
      hint.className = 'text-muted fw-normal';
    } else {
      hint.textContent = '*';
      hint.className = 'text-danger';
    }
  }

  function updatePaymentUI() {
    const status = document.getElementById('payment-status')?.value || 'paid';
    const grand = grandTotal();
    const paidEl = document.getElementById('amount-paid');
    const remainWrap = document.getElementById('remain-wrap');
    const advanceWrap = document.getElementById('advance-wrap');
    const paidWrap = document.getElementById('amount-paid-wrap');
    updateCustomerRequirement();

    if (status === 'unpaid') {
      paidEl.value = '0.00';
      paidEl.readOnly = true;
      paidWrap?.classList.add('d-none');
      remainWrap?.classList.remove('d-none');
      document.getElementById('remain-amount').value = money(grand);
      advanceWrap?.classList.add('d-none');
      return;
    }

    paidWrap?.classList.remove('d-none');

    if (status === 'paid') {
      // Paid in full = Amount Paid always matches Grand Total (sale line amounts).
      paidEl.value = money(grand);
      paidEl.readOnly = true;
      remainWrap?.classList.add('d-none');
      advanceWrap?.classList.add('d-none');
      return;
    }

    paidEl.readOnly = false;
    const paid = Number(paidEl.value || 0);
    const remain = Math.max(0, grand - paid);
    const advance = Math.max(0, paid - grand);

    if (status === 'partial' || remain > 0.001) {
      remainWrap?.classList.remove('d-none');
      document.getElementById('remain-amount').value = money(remain);
    } else {
      remainWrap?.classList.add('d-none');
    }

    if (advance > 0.001) {
      advanceWrap?.classList.remove('d-none');
      document.getElementById('advance-amount').value = money(advance);
    } else {
      advanceWrap?.classList.add('d-none');
    }
  }

  function recalc() {
    let sum = 0;
    tbody.querySelectorAll('tr').forEach((tr) => {
      if (isOpenSale(tr)) {
        const uw = rowUnitWeight(tr);
        const sw = Number(tr.querySelector('.sale-weight')?.value || 0);
        if (uw > 0) tr.querySelector('.qty').value = sw > 0 ? sw / uw : 0;
        if (tr.dataset.priceDirty !== '1') {
          tr.querySelector('.price').value = money(suggestedWeightAmount(tr));
        }
      } else if (hasUnitWeight(tr)) {
        const uw = rowUnitWeight(tr);
        const q = Number(tr.querySelector('.qty')?.value || 0);
        if (uw > 0) tr.querySelector('.sale-weight').value = q > 0 ? q * uw : '';
      }
      const line = lineAmount(tr);
      const cell = tr.querySelector('.line-total');
      if (cell) cell.textContent = money(line);
      sum += line;
    });
    let disc = discountAmount();
    if (disc > sum) {
      disc = sum;
      document.getElementById('sale-discount').value = money(disc);
    }
    const grand = Math.max(0, sum - disc);
    const subEl = document.getElementById('sale-subtotal');
    const discEl = document.getElementById('sale-discount-display');
    if (subEl) subEl.textContent = money(sum);
    if (discEl) discEl.textContent = money(disc);
    document.getElementById('sale-grand-total').textContent = money(grand);
    updatePaymentUI();
  }

  function addRow(prefill) {
    const tr = rowTemplate();
    tbody.appendChild(tr);
    if (prefill) {
      fillProductOnRow(tr, {
        ...prefill,
        _fromEdit: !!prefill._fromEdit,
        sale_price: prefill.list_unit_price ?? prefill.sale_price ?? prefill.unit_price,
        list_price: prefill.list_unit_price ?? prefill.list_price,
      }, { keepAmount: !!prefill._fromEdit });
    }
    bindRow(tr);
    recalc();
    return tr;
  }

  function bindRow(tr) {
    tr.querySelector('.qty')?.addEventListener('input', () => {
      tr.dataset.priceDirty = '0';
      recalc();
    });
    tr.querySelector('.sale-weight')?.addEventListener('input', () => {
      tr.dataset.priceDirty = '0';
      recalc();
    });
    tr.querySelector('.sale-mode')?.addEventListener('change', () => {
      const list = Number(tr.querySelector('.list-price')?.value || 0);
      const uw = rowUnitWeight(tr);
      if (isOpenSale(tr)) {
        let sw = Number(tr.querySelector('.sale-weight')?.value || 0);
        const q = Number(tr.querySelector('.qty')?.value || 0);
        if (!(sw > 0) && uw > 0 && q > 0) sw = q * uw;
        if (!(sw > 0) && uw > 0) sw = uw;
        tr.querySelector('.sale-weight').value = sw || '';
        tr.querySelector('.price').value = money(suggestedWeightAmount(tr));
      } else {
        let q = Number(tr.querySelector('.qty')?.value || 0);
        const sw = Number(tr.querySelector('.sale-weight')?.value || 0);
        if (!(q > 0) && uw > 0 && sw > 0) q = sw / uw;
        if (!(q > 0)) q = 1;
        // Prefer whole units when switching to Full
        if (Math.abs(q - Math.round(q)) < 0.001) q = Math.round(q);
        tr.querySelector('.qty').value = q;
        tr.querySelector('.price').value = money(list);
      }
      tr.dataset.priceDirty = '0';
      applyRowMode(tr);
      recalc();
    });
    tr.querySelector('.price')?.addEventListener('input', () => {
      tr.dataset.priceDirty = '1';
      recalc();
    });
    tr.querySelector('.btn-remove-row')?.addEventListener('click', () => {
      if (tbody.rows.length > 1) tr.remove();
      recalc();
    });
    tr.querySelector('.btn-quick-product')?.addEventListener('click', () => {
      activeRow = tr;
      const q = tr.querySelector('.product-search')?.value?.trim();
      if (q) document.getElementById('qp-name').value = q;
      window.PhotoPicker?.clear?.(document.getElementById('qp-photo-picker'));
      bootstrap.Modal.getOrCreateInstance(document.getElementById('quickProductModal')).show();
    });

    const search = tr.querySelector('.product-search');
    const results = tr.querySelector('.product-results');
    search?.addEventListener('input', () => {
      clearTimeout(productTimer);
      const q = search.value.trim();
      tr.querySelector('.product-id').value = '';
      tr.querySelector('.product-unit-weight').value = '';
      tr.querySelector('.product-weight-unit').value = '';
      setSaleMode(tr, 'full');
      applyRowMode(tr);
      setProductThumb(tr, null);
      if (q.length < 1) {
        results.classList.add('d-none');
        return;
      }
      productTimer = setTimeout(async () => {
        try {
          const res = await fetch('/api/products/lookup?q=' + encodeURIComponent(q));
          const data = await res.json();
          const rows = data.results || [];
          if (!rows.length) {
            results.innerHTML = `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add "${q}"</button>`;
          } else {
            results.innerHTML =
              rows
                .map((p) => {
                  return `<button type="button" class="lookup-item"
                      data-id="${p.id}"
                      data-name="${p.name}"
                      data-price="${p.sale_price}"
                      data-list="${p.list_price ?? p.sale_price}"
                      data-unit-weight="${p.unit_weight || ''}"
                      data-weight-unit="${p.weight_unit || ''}"
                      data-photo="${p.photo_url || ''}">${p.label}</button>`;
                })
                .join('') +
              `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add new product</button>`;
          }
          results.classList.remove('d-none');
        } catch (_) {
          results.innerHTML = `<div class="p-2 text-danger small">Search failed. Try again.</div>`;
          results.classList.remove('d-none');
        }
      }, 200);
    });

    results?.addEventListener('click', (e) => {
      const btn = e.target.closest('.lookup-item');
      if (!btn) return;
      if (btn.classList.contains('lookup-create')) {
        activeRow = tr;
        document.getElementById('qp-name').value = btn.dataset.name || '';
        results.classList.add('d-none');
        window.PhotoPicker?.clear?.(document.getElementById('qp-photo-picker'));
        bootstrap.Modal.getOrCreateInstance(document.getElementById('quickProductModal')).show();
        return;
      }
      fillProductOnRow(tr, {
        id: btn.dataset.id,
        name: btn.dataset.name,
        sale_price: btn.dataset.price,
        list_price: btn.dataset.list || btn.dataset.price,
        unit_weight: btn.dataset.unitWeight || '',
        weight_unit: btn.dataset.weightUnit || '',
        photo_url: btn.dataset.photo || null,
        sale_mode: 'full',
      });
      results.classList.add('d-none');
      recalc();
    });
  }

  function resetSaleModal() {
    editingSaleId = null;
    const title = document.getElementById('sale-modal-title');
    if (title) title.textContent = 'Add Sale';
    const saveBtn = document.getElementById('btn-save-sale');
    if (saveBtn) saveBtn.textContent = 'Save Sale';
    document.getElementById('sale-date').value =
      window.getErpWorkingDate?.() ||
      document.body?.dataset?.workingDate ||
      document.getElementById('sale-date').dataset.today ||
      '';
    document.getElementById('customer-id').value = '';
    document.getElementById('customer-search').value = '';
    document.getElementById('customer-selected').textContent = 'Walk-in';
    document.getElementById('salesman-id').value = '';
    document.getElementById('salesman-search').value = '';
    document.getElementById('salesman-selected').textContent = 'None';
    document.getElementById('payment-status').value = 'paid';
    document.getElementById('sale-discount').value = '0';
    document.getElementById('amount-paid').value = '0';
    document.getElementById('sale-notes').value = '';
    const err = document.getElementById('sale-error');
    if (err) {
      err.classList.add('d-none');
      err.textContent = '';
    }
    tbody.innerHTML = '';
    addRow();
    recalc();
    updateCustomerRequirement();
  }

  async function openSaleForEdit(saleId) {
    const err = document.getElementById('sale-error');
    err?.classList.add('d-none');
    try {
      const res = await fetch('/sales/' + saleId);
      const data = await res.json();
      if (!data.ok || !data.sale) {
        window.alert(data.error || 'Could not load sale.');
        return;
      }
      const s = data.sale;
      editingSaleId = s.id;
      const title = document.getElementById('sale-modal-title');
      if (title) title.textContent = 'Edit Sale ' + (s.invoice_no || '');
      const saveBtn = document.getElementById('btn-save-sale');
      if (saveBtn) saveBtn.textContent = 'Update Sale';

      document.getElementById('sale-date').value = s.sale_date || '';
      document.getElementById('customer-id').value = s.customer_id || '';
      document.getElementById('customer-search').value = s.customer_id ? s.customer_name || '' : '';
      document.getElementById('customer-selected').textContent = s.customer_name || 'Walk-in';
      document.getElementById('salesman-id').value = s.salesman_id || '';
      document.getElementById('salesman-search').value = s.salesman_id ? s.salesman_name || '' : '';
      document.getElementById('salesman-selected').textContent = s.salesman_name || 'None';
      document.getElementById('payment-status').value = s.payment_status || 'paid';
      document.getElementById('sale-discount').value = money(s.discount || 0);
      document.getElementById('sale-notes').value = s.notes || '';

      tbody.innerHTML = '';
      (s.items || []).forEach((it) =>
        addRow({
          ...it,
          id: it.product_id,
          sale_price: it.list_unit_price,
          list_price: it.list_unit_price,
          _fromEdit: true,
        })
      );
      if (!tbody.rows.length) addRow();

      document.getElementById('amount-paid').value = money(s.amount_paid || 0);
      recalc();
      if ((s.payment_status || '') !== 'paid') {
        document.getElementById('amount-paid').value = money(s.amount_paid || 0);
        updatePaymentUI();
      }

      bootstrap.Modal.getOrCreateInstance(document.getElementById('saleModal')).show();
    } catch (_) {
      window.alert('Network error loading sale.');
    }
  }

  document.getElementById('btn-add-row')?.addEventListener('click', () => addRow());

  document.getElementById('btn-add-sale')?.addEventListener('click', () => {
    resetSaleModal();
  });

  document.getElementById('saleModal')?.addEventListener('show.bs.modal', (e) => {
    // Opening via Add Sale button already resets; skip when edit loaded the form
    if (e.relatedTarget && e.relatedTarget.id === 'btn-add-sale') {
      resetSaleModal();
    }
  });

  document.getElementById('saleModal')?.addEventListener('hidden.bs.modal', () => {
    editingSaleId = null;
  });

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-edit-sale');
    if (!btn) return;
    e.preventDefault();
    openSaleForEdit(btn.dataset.id);
  });

  // Customer search
  const custSearch = document.getElementById('customer-search');
  const custResults = document.getElementById('customer-results');
  custSearch?.addEventListener('input', () => {
    clearTimeout(customerTimer);
    const q = custSearch.value.trim();
    document.getElementById('customer-id').value = '';
    document.getElementById('customer-selected').textContent = 'Walk-in';
    if (q.length < 1) {
      custResults.classList.add('d-none');
      return;
    }
    customerTimer = setTimeout(async () => {
      const res = await fetch('/api/customers/lookup?q=' + encodeURIComponent(q));
      const data = await res.json();
      if (!data.results.length) {
        custResults.innerHTML = `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add "${q}"</button>`;
      } else {
        custResults.innerHTML =
          data.results
            .map(
              (c) =>
                `<button type="button" class="lookup-item" data-id="${c.id}" data-name="${c.name}">${c.label}</button>`
            )
            .join('') +
          `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add new customer</button>`;
      }
      custResults.classList.remove('d-none');
    }, 200);
  });

  custResults?.addEventListener('click', (e) => {
    const btn = e.target.closest('.lookup-item');
    if (!btn) return;
    if (btn.classList.contains('lookup-create')) {
      openQuickCustomer(btn.dataset.name || '');
      custResults.classList.add('d-none');
      return;
    }
    document.getElementById('customer-id').value = btn.dataset.id;
    document.getElementById('customer-selected').textContent = btn.dataset.name;
    custSearch.value = btn.dataset.name;
    custResults.classList.add('d-none');
  });

  function openQuickCustomer(prefillName) {
    document.getElementById('qc-name').value = prefillName || '';
    document.getElementById('qc-phone').value = '';
    document.getElementById('qc-address').value = '';
    document.getElementById('qc-book').value = '';
    document.getElementById('qc-balance').value = '';
    const err = document.getElementById('qc-error');
    if (err) {
      err.classList.add('d-none');
      err.textContent = '';
    }
    window.PhotoPicker?.clear?.(document.getElementById('qc-photo-picker'));
    bootstrap.Modal.getOrCreateInstance(document.getElementById('quickCustomerModal')).show();
  }

  document.getElementById('btn-add-customer')?.addEventListener('click', () => {
    openQuickCustomer(custSearch.value.trim());
  });

  // Salesman search (optional field officer)
  let salesmanTimer = null;
  const smSearch = document.getElementById('salesman-search');
  const smResults = document.getElementById('salesman-results');
  smSearch?.addEventListener('input', () => {
    clearTimeout(salesmanTimer);
    const q = smSearch.value.trim();
    document.getElementById('salesman-id').value = '';
    document.getElementById('salesman-selected').textContent = 'None';
    if (q.length < 1) {
      smResults.classList.add('d-none');
      return;
    }
    salesmanTimer = setTimeout(async () => {
      const res = await fetch('/api/salesmen/lookup?q=' + encodeURIComponent(q));
      const data = await res.json();
      if (!data.results.length) {
        smResults.innerHTML = `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add "${q}"</button>`;
      } else {
        smResults.innerHTML =
          data.results
            .map(
              (s) =>
                `<button type="button" class="lookup-item" data-id="${s.id}" data-name="${s.name}">${s.label}</button>`
            )
            .join('') +
          `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add new salesman</button>`;
      }
      smResults.classList.remove('d-none');
    }, 200);
  });

  smResults?.addEventListener('click', (e) => {
    const btn = e.target.closest('.lookup-item');
    if (!btn) return;
    if (btn.classList.contains('lookup-create')) {
      openQuickSalesman(btn.dataset.name || '');
      smResults.classList.add('d-none');
      return;
    }
    document.getElementById('salesman-id').value = btn.dataset.id;
    document.getElementById('salesman-selected').textContent = btn.dataset.name;
    smSearch.value = btn.dataset.name;
    smResults.classList.add('d-none');
  });

  function openQuickSalesman(prefillName) {
    document.getElementById('qs-name').value = prefillName || '';
    document.getElementById('qs-phone').value = '';
    document.getElementById('qs-company').value = '';
    document.getElementById('qs-address').value = '';
    document.getElementById('qs-balance').value = '';
    const err = document.getElementById('qs-error');
    if (err) {
      err.classList.add('d-none');
      err.textContent = '';
    }
    window.PhotoPicker?.clear?.(document.getElementById('qs-photo-picker'));
    bootstrap.Modal.getOrCreateInstance(document.getElementById('quickSalesmanModal')).show();
  }

  document.getElementById('btn-add-salesman')?.addEventListener('click', () => {
    openQuickSalesman(smSearch.value.trim());
  });

  document.getElementById('qs-save')?.addEventListener('click', async () => {
    const err = document.getElementById('qs-error');
    err.classList.add('d-none');
    const payload = {
      name: document.getElementById('qs-name').value.trim(),
      phone: document.getElementById('qs-phone').value.trim(),
      company: document.getElementById('qs-company').value.trim(),
      address: document.getElementById('qs-address').value.trim(),
      opening_balance: document.getElementById('qs-balance').value || 0,
      photo: document.querySelector('#qs-photo-picker .photo-path')?.value || '',
    };
    if (!payload.name) {
      err.textContent = 'Salesman name is required.';
      err.classList.remove('d-none');
      return;
    }
    const res = await fetch('/api/salesmen/quick', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) {
      err.textContent = data.error || 'Could not save salesman';
      err.classList.remove('d-none');
      return;
    }
    document.getElementById('salesman-id').value = data.id;
    document.getElementById('salesman-search').value = data.name;
    document.getElementById('salesman-selected').textContent = data.name;
    bootstrap.Modal.getInstance(document.getElementById('quickSalesmanModal'))?.hide();
  });

  document.getElementById('payment-status')?.addEventListener('change', () => {
    const status = document.getElementById('payment-status').value;
    const paidEl = document.getElementById('amount-paid');
    const grand = grandTotal();
    if (status === 'partial') {
      const cur = Number(paidEl.value || 0);
      if (cur <= 0 || cur >= grand) paidEl.value = money(grand / 2);
    }
    updatePaymentUI();
  });

  document.getElementById('amount-paid')?.addEventListener('input', updatePaymentUI);
  document.getElementById('sale-discount')?.addEventListener('input', recalc);

  document.getElementById('qc-save')?.addEventListener('click', async () => {
    const err = document.getElementById('qc-error');
    err.classList.add('d-none');
    const payload = {
      name: document.getElementById('qc-name').value.trim(),
      phone: document.getElementById('qc-phone').value.trim(),
      address: document.getElementById('qc-address').value.trim(),
      old_book_no: document.getElementById('qc-book').value.trim(),
      opening_balance: document.getElementById('qc-balance').value || 0,
      joined_date: document.getElementById('qc-date').value,
      customer_type: document.getElementById('qc-type')?.value || 'good',
      photo: document.querySelector('#qc-photo-picker .photo-path')?.value || '',
    };
    if (!payload.name) {
      err.textContent = 'Customer name is required.';
      err.classList.remove('d-none');
      return;
    }
    const res = await fetch('/api/customers/quick', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) {
      err.textContent = data.error || 'Could not save customer';
      err.classList.remove('d-none');
      return;
    }
    document.getElementById('customer-id').value = data.id;
    document.getElementById('customer-selected').textContent = data.name;
    custSearch.value = data.name;
    bootstrap.Modal.getInstance(document.getElementById('quickCustomerModal'))?.hide();
  });

  let qpNameTimer = null;
  function fillQuickProductFromExisting(p) {
    const idEl = document.getElementById('qp-existing-id');
    if (idEl) idEl.value = p.id || '';
    const nameEl = document.getElementById('qp-name');
    if (nameEl) nameEl.value = p.name || '';
    const sku = document.getElementById('qp-sku');
    if (sku) {
      sku.value = p.sku || '';
      sku.readOnly = !!p.id;
    }
    const barcode = document.getElementById('qp-barcode');
    if (barcode) barcode.value = p.barcode || '';
    const brand = document.getElementById('qp-brand');
    if (brand) brand.value = p.brand || '';
    const catId = document.getElementById('qp-category');
    const catSearch = document.getElementById('qp-cat-search');
    if (catId) catId.value = p.category_id || '';
    if (catSearch) catSearch.value = p.category_name || '';
    const subId = document.getElementById('qp-subcategory');
    const subSearch = document.getElementById('qp-subcat-search');
    if (subId) subId.value = p.subcategory_id || '';
    if (subSearch) subSearch.value = p.subcategory_name || '';
    const purchase = document.getElementById('qp-purchase');
    if (purchase) purchase.value = money(p.purchase_price || 0);
    const sale = document.getElementById('qp-price');
    if (sale) sale.value = money(p.list_price != null ? p.list_price : p.sale_price || 0);
    const min = document.getElementById('qp-min-stock');
    if (min) min.value = p.minimum_stock != null ? p.minimum_stock : 0;
    const uw = document.getElementById('qp-unit-weight');
    if (uw) uw.value = p.unit_weight != null && p.unit_weight !== '' ? p.unit_weight : '';
    const wu = document.getElementById('qp-weight-unit');
    if (wu) wu.value = p.weight_unit || '';
    const desc = document.getElementById('qp-description');
    if (desc) desc.value = p.description || '';
    const batch = document.getElementById('qp-batch');
    if (batch) batch.value = `#${p.next_batch || 1}`;
  }

  document.getElementById('qp-name')?.addEventListener('input', () => {
    clearTimeout(qpNameTimer);
    const results = document.getElementById('qp-name-results');
    const idEl = document.getElementById('qp-existing-id');
    const q = document.getElementById('qp-name')?.value?.trim() || '';
    if (idEl) idEl.value = '';
    const sku = document.getElementById('qp-sku');
    if (sku) sku.readOnly = false;
    const batch = document.getElementById('qp-batch');
    if (batch) batch.value = '#1';
    if (!results) return;
    if (q.length < 1) {
      results.classList.add('d-none');
      return;
    }
    qpNameTimer = setTimeout(async () => {
      try {
        const res = await fetch('/api/products/lookup?q=' + encodeURIComponent(q));
        const data = await res.json();
        const rows = data.results || [];
        if (!rows.length) {
          results.innerHTML = '<div class="p-2 text-muted small">New product — will be created on save</div>';
        } else {
          results.innerHTML = rows
            .map(
              (p) =>
                `<button type="button" class="lookup-item" data-id="${p.id}"
                  data-name="${p.name || ''}" data-sku="${p.sku || ''}" data-barcode="${p.barcode || ''}"
                  data-brand="${p.brand || ''}" data-category-id="${p.category_id || ''}"
                  data-category-name="${p.category_name || ''}" data-subcategory-id="${p.subcategory_id || ''}"
                  data-subcategory-name="${p.subcategory_name || ''}" data-purchase="${p.purchase_price || 0}"
                  data-sale="${p.list_price != null ? p.list_price : p.sale_price || 0}" data-min="${p.minimum_stock || 0}"
                  data-unit-weight="${p.unit_weight || ''}" data-weight-unit="${p.weight_unit || ''}"
                  data-next-batch="${p.next_batch || 1}">
                  Restock: ${p.label}</button>`
            )
            .join('');
        }
        results.classList.remove('d-none');
      } catch (_) {
        results.classList.add('d-none');
      }
    }, 200);
  });

  document.getElementById('qp-name-results')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.lookup-item');
    if (!btn) return;
    fillQuickProductFromExisting({
      id: btn.dataset.id,
      name: btn.dataset.name,
      sku: btn.dataset.sku,
      barcode: btn.dataset.barcode,
      brand: btn.dataset.brand,
      category_id: btn.dataset.categoryId,
      category_name: btn.dataset.categoryName,
      subcategory_id: btn.dataset.subcategoryId,
      subcategory_name: btn.dataset.subcategoryName,
      purchase_price: btn.dataset.purchase,
      sale_price: btn.dataset.sale,
      list_price: btn.dataset.sale,
      minimum_stock: btn.dataset.min,
      unit_weight: btn.dataset.unitWeight,
      weight_unit: btn.dataset.weightUnit,
      next_batch: Number(btn.dataset.nextBatch || 1),
    });
    document.getElementById('qp-name-results')?.classList.add('d-none');
  });

  document.getElementById('quickProductModal')?.addEventListener('show.bs.modal', () => {
    const idEl = document.getElementById('qp-existing-id');
    if (idEl) idEl.value = '';
    const sku = document.getElementById('qp-sku');
    if (sku) sku.readOnly = false;
  });

  document.getElementById('qp-save')?.addEventListener('click', async () => {
    const err = document.getElementById('qp-error');
    err.classList.add('d-none');

    if (window.VendorLookup) {
      const vendor = VendorLookup.ensureSelected('qp-vendor-id', true);
      if (!vendor.ok) {
        err.textContent = vendor.error || 'Vendor is required';
        err.classList.remove('d-none');
        return;
      }
    } else if (!document.getElementById('qp-vendor-id')?.value) {
      err.textContent = 'Vendor is required';
      err.classList.remove('d-none');
      return;
    }

    const stockVal = Number(document.getElementById('qp-stock').value || 0);
    if (!(stockVal > 0)) {
      err.textContent = 'Purchase quantity is required.';
      err.classList.remove('d-none');
      return;
    }

    if (window.CategoryLookup) {
      const cat = await CategoryLookup.ensureSelected('qp-cat-search', 'qp-category', null);
      if (!cat.ok) {
        err.textContent = cat.error || 'Category is required';
        err.classList.remove('d-none');
        return;
      }
      const subName = document.getElementById('qp-subcat-search')?.value?.trim();
      if (subName) {
        const sub = await CategoryLookup.ensureSelected('qp-subcat-search', 'qp-subcategory', cat.id);
        if (!sub.ok) {
          err.textContent = sub.error || 'Could not save subcategory';
          err.classList.remove('d-none');
          return;
        }
      } else {
        const subEl = document.getElementById('qp-subcategory');
        if (subEl) subEl.value = '';
      }
    }

    const payload = {
      name: document.getElementById('qp-name').value.trim(),
      sku: document.getElementById('qp-sku').value.trim(),
      barcode: document.getElementById('qp-barcode')?.value.trim() || '',
      category_id: document.getElementById('qp-category')?.value || '',
      subcategory_id: document.getElementById('qp-subcategory')?.value || 0,
      brand: document.getElementById('qp-brand')?.value.trim() || '',
      vendor_id: document.getElementById('qp-vendor-id')?.value || '',
      invoice_no: document.getElementById('qp-invoice')?.value.trim() || '',
      sale_price: document.getElementById('qp-price').value,
      purchase_price: document.getElementById('qp-purchase').value,
      opening_stock: document.getElementById('qp-stock').value,
      minimum_stock: document.getElementById('qp-min-stock')?.value || 0,
      unit_weight: document.getElementById('qp-unit-weight')?.value || '',
      weight_unit: document.getElementById('qp-weight-unit')?.value || '',
      existing_product_id: document.getElementById('qp-existing-id')?.value || '',
      batch_number: '',
      expiry_date: document.getElementById('qp-expiry')?.value || '',
      description: document.getElementById('qp-description')?.value.trim() || '',
      photo: document.querySelector('#qp-photo-picker .photo-path')?.value || '',
    };
    const res = await fetch('/api/products/quick', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) {
      err.textContent = data.error || 'Could not save product';
      err.classList.remove('d-none');
      return;
    }
    if (activeRow) {
      fillProductOnRow(activeRow, {
        id: data.id,
        name: data.name,
        sale_price: data.sale_price,
        list_price: data.list_price ?? data.sale_price,
        unit_weight: data.unit_weight || '',
        weight_unit: data.weight_unit || '',
        photo_url: data.photo_url || null,
      });
      activeRow.querySelector('.product-results')?.classList.add('d-none');
      recalc();
    }
    bootstrap.Modal.getInstance(document.getElementById('quickProductModal'))?.hide();
  });

  if (window.VendorLookup) {
    VendorLookup.bind({
      searchId: 'qp-vendor-search',
      idId: 'qp-vendor-id',
      resultsId: 'qp-vendor-results',
      addBtnId: 'qp-btn-add-vendor',
      modalId: 'quickVendorModal',
      required: true,
    });
  }

  if (window.CategoryLookup) {
    CategoryLookup.bind({
      searchId: 'qp-cat-search',
      idId: 'qp-category',
      resultsId: 'qp-cat-results',
      label: 'category',
      onChange: () => {
        const subId = document.getElementById('qp-subcategory');
        const subSearch = document.getElementById('qp-subcat-search');
        if (subId) subId.value = '';
        if (subSearch) subSearch.value = '';
      },
    });
    CategoryLookup.bind({
      searchId: 'qp-subcat-search',
      idId: 'qp-subcategory',
      resultsId: 'qp-subcat-results',
      label: 'subcategory',
      requireParent: true,
      parentIdGetter: () => document.getElementById('qp-category')?.value || '',
    });
  }

  document.getElementById('btn-save-sale')?.addEventListener('click', async () => {
    const err = document.getElementById('sale-error');
    err.classList.add('d-none');
    const items = [];
    tbody.querySelectorAll('tr').forEach((tr) => {
      const pid = tr.querySelector('.product-id')?.value;
      if (!pid) return;
      const listPrice = Number(tr.querySelector('.list-price')?.value || 0);
      const lineTotal = lineAmount(tr);
      const item = {
        product_id: Number(pid),
        list_unit_price: listPrice,
        line_total: lineTotal,
      };
      if (isOpenSale(tr)) {
        item.sale_mode = 'open';
        item.sale_weight = Number(tr.querySelector('.sale-weight')?.value || 0);
        item.quantity = Number(tr.querySelector('.qty')?.value || 0);
        item.unit_price = lineTotal;
        item.weight_unit = tr.querySelector('.product-weight-unit')?.value || '';
        if (hasUnitWeight(tr)) {
          item.unit_weight = rowUnitWeight(tr);
        }
      } else {
        item.sale_mode = hasUnitWeight(tr) ? 'full' : 'qty';
        item.quantity = Number(tr.querySelector('.qty')?.value || 0);
        item.unit_price = Number(tr.querySelector('.price')?.value || 0);
        if (hasUnitWeight(tr)) {
          item.unit_weight = rowUnitWeight(tr);
          item.weight_unit = tr.querySelector('.product-weight-unit')?.value || '';
        }
      }
      items.push(item);
    });
    if (!items.length) {
      err.textContent = 'Add at least one product line.';
      err.classList.remove('d-none');
      return;
    }
    const paymentStatus = document.getElementById('payment-status').value;
    const customerId = document.getElementById('customer-id').value || null;
    const salesmanId = document.getElementById('salesman-id').value || null;
    if ((paymentStatus === 'unpaid' || paymentStatus === 'partial') && !customerId) {
      err.textContent = 'Select a customer for credit or partial sales.';
      err.classList.remove('d-none');
      return;
    }
    const payload = {
      sale_date: document.getElementById('sale-date').value,
      customer_id: customerId,
      salesman_id: salesmanId,
      payment_status: paymentStatus,
      amount_paid: document.getElementById('amount-paid').value,
      discount: document.getElementById('sale-discount')?.value || 0,
      notes: document.getElementById('sale-notes').value,
      items,
    };
    const btn = document.getElementById('btn-save-sale');
    btn.disabled = true;
    const url = editingSaleId ? `/sales/${editingSaleId}/replace` : '/sales/create';
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.ok) {
        err.textContent = data.error || 'Sale failed';
        err.classList.remove('d-none');
        return;
      }
      bootstrap.Modal.getInstance(document.getElementById('saleModal'))?.hide();
      const invoiceUrl = data.invoice_url || '/sales/' + data.sale_id + '/invoice';
      const sep = invoiceUrl.includes('?') ? '&' : '?';
      window.location.href = invoiceUrl + sep + 'next=' + encodeURIComponent('/sales/');
    } catch (e) {
      err.textContent = 'Network error. Try again.';
      err.classList.remove('d-none');
    } finally {
      btn.disabled = false;
    }
  });

  ['quickCustomerModal', 'quickProductModal', 'quickVendorModal'].forEach((id) => {
    const el = document.getElementById(id);
    el?.addEventListener('show.bs.modal', async () => {
      document.getElementById('saleModal')?.classList.add('modal-nested-open');
      if (id === 'quickProductModal') {
        try {
          const res = await fetch('/api/products/next-codes');
          const data = await res.json();
          if (data.ok) {
            const sku = document.getElementById('qp-sku');
            const barcode = document.getElementById('qp-barcode');
            const batch = document.getElementById('qp-batch');
            if (sku) sku.value = data.sku || '';
            if (barcode) barcode.value = data.barcode || '';
            if (batch && data.next_batch_id != null) batch.value = `#${data.next_batch_id}`;
          }
        } catch (_) {
          /* server will auto-fill on save if left empty */
        }
      }
    });
    el?.addEventListener('hidden.bs.modal', () => {
      const saleModal = document.getElementById('saleModal');
      if (id === 'quickVendorModal') {
        // Return to product modal after nested vendor add
        const qp = document.getElementById('quickProductModal');
        if (qp && !qp.classList.contains('show')) {
          bootstrap.Modal.getOrCreateInstance(qp).show();
        }
        return;
      }
      saleModal?.classList.remove('modal-nested-open');
      if (saleModal && !saleModal.classList.contains('show')) {
        bootstrap.Modal.getOrCreateInstance(saleModal).show();
      }
    });
  });

  if (tbody && !tbody.rows.length) addRow();
  updateCustomerRequirement();
  if (window.OPEN_SALE_MODAL) {
    resetSaleModal();
    bootstrap.Modal.getOrCreateInstance(document.getElementById('saleModal')).show();
  }
  if (window.location.search.includes('open_modal=')) {
    const url = new URL(window.location.href);
    url.searchParams.delete('open_modal');
    window.history.replaceState({}, '', url.pathname + (url.search || '') + url.hash);
  }

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.lookup-results') && !e.target.closest('.product-search') && !e.target.closest('#customer-search')) {
      document.querySelectorAll('.lookup-results').forEach((el) => el.classList.add('d-none'));
    }
  });
})();
