(function () {
  const csrf = () => window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;
  const money = (n) => Number(n || 0).toFixed(2);
  const tbody = document.querySelector('#sale-lines tbody');
  let activeRow = null;
  let customerTimer = null;
  let productTimer = null;
  let editingSaleId = null;
  let allowNegativeStock = false;
  let pendingZeroStock = null; // { mode:'select', tr, product } | { mode:'save', payload, url, productName }

  function headers() {
    return {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf(),
    };
  }

  function openQuickProductModal(productName) {
    window._pendingSaleProductName = productName || '';
    window.ErpModalNesting?.onChildShow?.('quickProductModal');
    bootstrap.Modal.getOrCreateInstance(document.getElementById('quickProductModal')).show();
  }

  window.onInventoryProductSaved = function (data) {
    const products = data.products || [];
    const product =
      products[0] ||
      (data.id
        ? {
            id: data.id,
            name: data.name,
            sale_price: data.sale_price,
            list_price: data.list_price,
            unit_weight: data.unit_weight,
            weight_unit: data.weight_unit,
            photo_url: data.photo_url,
          }
        : null);
    if (product && activeRow) {
      fillProductOnRow(activeRow, {
        id: product.id,
        name: product.name,
        sale_price: product.sale_price,
        list_price: product.list_price ?? product.sale_price,
        unit_weight: product.unit_weight || '',
        weight_unit: product.weight_unit || '',
        photo_url: product.photo_url || null,
      });
      activeRow.querySelector('.product-results')?.classList.add('d-none');
      recalc();
    }
    bootstrap.Modal.getInstance(document.getElementById('quickProductModal'))?.hide();
  };

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
    const stock = Number(p.stock ?? p.current_stock ?? 0);
    tr.dataset.stock = String(stock);
    if (p.allow_negative) {
      tr.dataset.allowNegative = '1';
      allowNegativeStock = true;
    }
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
      if (tbody && tbody.rows.length > 1) tr.remove();
      recalc();
    });
    tr.querySelector('.btn-quick-product')?.addEventListener('click', () => {
      activeRow = tr;
      openQuickProductModal(tr.querySelector('.product-search')?.value?.trim() || '');
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
          const res = await fetch('/api/products/lookup?q=' + encodeURIComponent(q) + '&active=active');
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
                      data-stock="${p.stock != null ? p.stock : 0}"
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
        results.classList.add('d-none');
        openQuickProductModal(btn.dataset.name || '');
        return;
      }
      const productPayload = {
        id: btn.dataset.id,
        name: btn.dataset.name,
        sale_price: btn.dataset.price,
        list_price: btn.dataset.list || btn.dataset.price,
        unit_weight: btn.dataset.unitWeight || '',
        weight_unit: btn.dataset.weightUnit || '',
        photo_url: btn.dataset.photo || null,
        stock: Number(btn.dataset.stock || 0),
        sale_mode: 'full',
      };
      results.classList.add('d-none');
      if (Number(productPayload.stock || 0) <= 0) {
        showZeroStockWarning(tr, productPayload);
        return;
      }
      fillProductOnRow(tr, productPayload);
      recalc();
    });
  }

  function showZeroStockWarning(tr, productPayload) {
    pendingZeroStock = { mode: 'select', tr, product: productPayload };
    const nameEl = document.getElementById('zero-stock-product-name');
    if (nameEl) nameEl.textContent = productPayload.name || 'This product';
    const msg = document.getElementById('zero-stock-message');
    if (msg) {
      msg.textContent =
        'This product is not available in stock. Add stock, cancel, or continue anyway (stock will go negative until the next purchase).';
    }
    const saleModal = document.getElementById('saleModal');
    saleModal?.classList.add('modal-nested-open');
    bootstrap.Modal.getOrCreateInstance(document.getElementById('zeroStockWarningModal')).show();
  }

  function showInsufficientStockWarning(serverMsg, payload, url) {
    const match = String(serverMsg || '').match(/insufficient stock(?: weight)? for (.+)$/i);
    const productName = (match && match[1] ? match[1].trim() : '') || 'This product';
    pendingZeroStock = { mode: 'save', payload, url, productName };
    const nameEl = document.getElementById('zero-stock-product-name');
    if (nameEl) nameEl.textContent = productName;
    const msg = document.getElementById('zero-stock-message');
    if (msg) {
      msg.textContent =
        'Not enough stock for this sale. Add product/stock, cancel, or continue anyway (stock will go negative until the next purchase covers it).';
    }
    document.getElementById('saleModal')?.classList.add('modal-nested-open');
    bootstrap.Modal.getOrCreateInstance(document.getElementById('zeroStockWarningModal')).show();
  }

  async function submitSalePayload(payload, url) {
    const err = document.getElementById('sale-error');
    const btn = document.getElementById('btn-save-sale');
    btn.disabled = true;
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.ok) {
        const msg = data.error || 'Sale failed';
        if (/insufficient stock/i.test(msg) && !payload.allow_negative_stock) {
          showInsufficientStockWarning(msg, payload, url);
          return;
        }
        err.textContent = msg;
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
  }

  function resetSaleModal() {
    editingSaleId = null;
    allowNegativeStock = false;
    pendingZeroStock = null;
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
    const modal = document.getElementById('quickCustomerModal');
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val;
    };
    setVal('qc-name', prefillName || '');
    setVal('qc-phone', '');
    setVal('qc-cnic', '');
    setVal('qc-address', '');
    setVal('qc-book', '');
    setVal('qc-balance', '');
    const err = document.getElementById('qc-error');
    if (err) {
      err.classList.add('d-none');
      err.textContent = '';
    }
    window.CustomerPhotos?.resetField?.(modal);
    window.ErpModalNesting?.onChildShow?.('quickCustomerModal');
    bootstrap.Modal.getOrCreateInstance(modal).show();
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
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val;
    };
    setVal('qs-name', prefillName || '');
    setVal('qs-phone', '');
    setVal('qs-company', '');
    setVal('qs-address', '');
    setVal('qs-balance', '');
    const err = document.getElementById('qs-error');
    if (err) {
      err.classList.add('d-none');
      err.textContent = '';
    }
    window.PhotoPicker?.clear?.(document.getElementById('qs-photo-picker'));
    window.ErpModalNesting?.onChildShow?.('quickSalesmanModal');
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
    err?.classList.add('d-none');
    const qcModal = document.getElementById('quickCustomerModal');
    const payload = {
      name: document.getElementById('qc-name')?.value.trim() || '',
      phone: document.getElementById('qc-phone')?.value.trim() || '',
      cnic: document.getElementById('qc-cnic')?.value.trim() || '',
      address: document.getElementById('qc-address')?.value.trim() || '',
      old_book_no: document.getElementById('qc-book')?.value.trim() || '',
      opening_balance: document.getElementById('qc-balance')?.value || 0,
      joined_date: document.getElementById('qc-date')?.value || '',
      customer_type: document.getElementById('qc-type')?.value || 'good',
      photo_paths: window.CustomerPhotos?.collectNewPaths?.(qcModal) || [],
    };
    if (!payload.name) {
      if (err) {
        err.textContent = 'Customer name is required.';
        err.classList.remove('d-none');
      }
      return;
    }
    const res = await fetch('/api/customers/quick', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) {
      if (err) {
        err.textContent = data.error || 'Could not save customer';
        err.classList.remove('d-none');
      }
      return;
    }
    document.getElementById('customer-id').value = data.id;
    document.getElementById('customer-selected').textContent = data.name;
    custSearch.value = data.name;
    bootstrap.Modal.getInstance(document.getElementById('quickCustomerModal'))?.hide();
  });

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
      if (tr.dataset.allowNegative === '1' || allowNegativeStock) {
        item.allow_negative_stock = true;
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
      allow_negative_stock: allowNegativeStock || items.some((i) => i.allow_negative_stock),
      items,
    };
    const url = editingSaleId ? `/sales/${editingSaleId}/replace` : '/sales/create';
    submitSalePayload(payload, url);
  });

  ['quickCustomerModal', 'quickSalesmanModal', 'quickProductModal', 'quickVendorModal'].forEach((id) => {
    window.ErpModalNesting?.bindQuickModal?.(id, (modalId) => {
      if (modalId === 'quickVendorModal') {
        const qp = document.getElementById('quickProductModal');
        if (qp && !qp.classList.contains('show')) {
          bootstrap.Modal.getOrCreateInstance(qp).show();
        }
        return;
      }
      const saleModal = document.getElementById('saleModal');
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

  // —— Sale return ——
  let returnMeta = null;

  function returnLineValue(tr) {
    const remValue = Number(tr.dataset.remValue || 0);
    const remQty = Number(tr.dataset.remQty || 0);
    const remW = tr.dataset.remWeight !== '' ? Number(tr.dataset.remWeight) : null;
    const isOpen = tr.dataset.isOpen === '1';
    if (isOpen && remW != null && remW > 0) {
      const rw = Number(tr.querySelector('.return-weight')?.value || 0);
      return remValue * (rw / remW);
    }
    const rq = Number(tr.querySelector('.return-qty')?.value || 0);
    return remQty > 0 ? remValue * (rq / remQty) : 0;
  }

  function applyReturnRefundSplit(total) {
    const cashEl = document.getElementById('return-cash');
    const creditEl = document.getElementById('return-credit');
    if (!cashEl || !creditEl) return;
    const maxCash = Number(returnMeta?.cash_refundable || 0);
    const status = (returnMeta?.payment_status || 'paid').toLowerCase();
    let cash;
    if (status === 'unpaid' || maxCash <= 0) {
      cash = 0;
    } else {
      // Paid / partial: refund cash first up to collected amount
      cash = Math.min(total, maxCash);
    }
    cashEl.value = money(cash);
    creditEl.value = money(Math.max(0, total - cash));
  }

  function recalcReturnTotals() {
    const rows = document.querySelectorAll('#return-lines tbody tr');
    let total = 0;
    rows.forEach((tr) => { total += returnLineValue(tr); });
    total = Math.round(total * 100) / 100;
    document.getElementById('return-total').value = money(total);
    applyReturnRefundSplit(total);
  }

  function clampReturnLineInput(tr) {
    if (!tr) return;
    const isOpen = tr.dataset.isOpen === '1';
    if (isOpen) {
      const inp = tr.querySelector('.return-weight');
      if (!inp) return;
      const max = Number(tr.dataset.remWeight || 0);
      let v = Number(inp.value);
      if (inp.value === '' || Number.isNaN(v)) return;
      if (v < 0) v = 0;
      if (v > max) v = max;
      inp.value = v;
    } else {
      const inp = tr.querySelector('.return-qty');
      if (!inp) return;
      const max = Number(tr.dataset.remQty || 0);
      let v = Number(inp.value);
      if (inp.value === '' || Number.isNaN(v)) return;
      if (v < 0) v = 0;
      if (v > max) v = max;
      inp.value = v;
    }
  }

  function fillReturnModal(data) {
    returnMeta = data;
    document.getElementById('return-sale-id').value = data.sale_id;
    document.getElementById('return-invoice-label').textContent = data.invoice_no || '';
    document.getElementById('return-customer').value = data.customer_name || 'Walk-in';
    document.getElementById('return-cash-hint').textContent =
      maxCashLabel(data.cash_refundable);
    const tbody = document.querySelector('#return-lines tbody');
    tbody.innerHTML = '';
    (data.items || []).forEach((it) => {
      const remQty = Number(it.quantity_remaining || 0);
      const remW = it.weight_remaining != null ? Number(it.weight_remaining) : null;
      const tr = document.createElement('tr');
      tr.dataset.saleItemId = it.sale_item_id;
      tr.dataset.remValue = it.remaining_value;
      tr.dataset.remQty = remQty;
      tr.dataset.remWeight = remW != null ? remW : '';
      tr.dataset.isOpen = it.is_open ? '1' : '0';
      const soldLabel = it.is_open && remW != null
        ? `${remW} ${it.weight_unit || 'kg'}`
        : String(remQty);
      let inputHtml;
      if (it.is_open && remW != null) {
        inputHtml = `<div class="input-group input-group-sm justify-content-end">
          <input type="number" step="0.001" min="0" max="${remW}" class="form-control form-control-sm text-end return-weight" value="0">
          <span class="input-group-text">${it.weight_unit || 'kg'}</span>
        </div>`;
      } else {
        inputHtml = `<input type="number" step="0.001" min="0" max="${remQty}" class="form-control form-control-sm text-end return-qty" value="0">`;
      }
      tr.innerHTML = `
        <td class="fw-semibold">${escapeHtml(it.product_name)}</td>
        <td class="text-end small">${soldLabel}</td>
        <td class="text-end" style="min-width:120px">${inputHtml}</td>
        <td class="text-end return-line-val">0.00</td>`;
      tbody.appendChild(tr);
    });
    document.getElementById('return-cash').value = '0.00';
    document.getElementById('return-credit').value = '0.00';
    document.getElementById('return-notes').value = '';
    document.getElementById('return-error')?.classList.add('d-none');
    const hint = document.getElementById('return-refund-hint');
    if (hint) {
      const st = (data.payment_status || 'paid').toLowerCase();
      if (st === 'unpaid') {
        hint.textContent = 'Original sale was unpaid — return goes to customer account credit (reduces due).';
      } else if (st === 'partial') {
        hint.textContent = 'Partial sale — cash refund limited to amount collected; remainder goes to account credit.';
      } else {
        hint.textContent = 'Paid sale — cash refund preferred up to amount paid; remainder to account credit if any.';
      }
    }
    recalcReturnTotals();
  }

  function maxCashLabel(n) {
    return `(max ${money(n)})`;
  }

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.btn-return-sale');
    if (!btn) return;
    const saleId = btn.dataset.id;
    const err = document.getElementById('return-error');
    err?.classList.add('d-none');
    try {
      const res = await fetch('/sales/' + saleId + '/returnable');
      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        alert('Could not open return. Please refresh the page and try again.');
        return;
      }
      if (!data.ok) {
        alert(data.error || 'Cannot open return');
        return;
      }
      if (!(data.items || []).length) {
        alert('Nothing left to return on this sale.');
        return;
      }
      fillReturnModal(data);
      const dateEl = document.getElementById('return-date');
      if (dateEl && !dateEl.value) {
        dateEl.value =
          window.getErpWorkingDate?.() ||
          document.body?.dataset?.workingDate ||
          dateEl.dataset.today ||
          '';
      }
      bootstrap.Modal.getOrCreateInstance(document.getElementById('returnSaleModal')).show();
    } catch (err2) {
      alert('Network error loading return.');
    }
  });

  document.getElementById('return-lines')?.addEventListener('input', (e) => {
    const tr = e.target.closest('tr');
    if (!tr) return;
    if (e.target.classList.contains('return-qty') || e.target.classList.contains('return-weight')) {
      clampReturnLineInput(tr);
    }
    const val = returnLineValue(tr);
    const cell = tr.querySelector('.return-line-val');
    if (cell) cell.textContent = money(val);
    recalcReturnTotals();
  });

  document.getElementById('return-lines')?.addEventListener('change', (e) => {
    const tr = e.target.closest('tr');
    if (!tr) return;
    if (e.target.classList.contains('return-qty') || e.target.classList.contains('return-weight')) {
      clampReturnLineInput(tr);
      const val = returnLineValue(tr);
      const cell = tr.querySelector('.return-line-val');
      if (cell) cell.textContent = money(val);
      recalcReturnTotals();
    }
  });

  document.getElementById('return-cash')?.addEventListener('input', () => {
    const total = Number(document.getElementById('return-total').value || 0);
    const maxCash = Number(returnMeta?.cash_refundable || 0);
    let cash = Number(document.getElementById('return-cash').value || 0);
    if (cash > maxCash) cash = maxCash;
    if (cash > total) cash = total;
    if (cash < 0) cash = 0;
    document.getElementById('return-credit').value = money(Math.max(0, total - cash));
  });

  document.getElementById('btn-return-all')?.addEventListener('click', () => {
    document.querySelectorAll('#return-lines tbody tr').forEach((tr) => {
      const isOpen = tr.dataset.isOpen === '1';
      if (isOpen) {
        const inp = tr.querySelector('.return-weight');
        if (inp) inp.value = tr.dataset.remWeight || 0;
      } else {
        const inp = tr.querySelector('.return-qty');
        if (inp) inp.value = tr.dataset.remQty || 0;
      }
      const cell = tr.querySelector('.return-line-val');
      if (cell) cell.textContent = money(returnLineValue(tr));
    });
    recalcReturnTotals();
  });

  document.getElementById('btn-save-return')?.addEventListener('click', async () => {
    const err = document.getElementById('return-error');
    err.classList.add('d-none');
    const saleId = document.getElementById('return-sale-id').value;
    const items = [];
    document.querySelectorAll('#return-lines tbody tr').forEach((tr) => {
      clampReturnLineInput(tr);
      const isOpen = tr.dataset.isOpen === '1';
      const maxQty = Number(tr.dataset.remQty || 0);
      const maxW = tr.dataset.remWeight !== '' ? Number(tr.dataset.remWeight) : null;
      const row = { sale_item_id: Number(tr.dataset.saleItemId) };
      if (isOpen) {
        let w = Number(tr.querySelector('.return-weight')?.value || 0);
        if (maxW != null && w > maxW) w = maxW;
        if (w > 0) row.sale_weight = w;
      } else {
        let q = Number(tr.querySelector('.return-qty')?.value || 0);
        if (q > maxQty) q = maxQty;
        if (q > 0) row.quantity = q;
      }
      if (row.quantity || row.sale_weight) items.push(row);
    });
    if (!items.length) {
      err.textContent = 'Enter a return quantity or weight on at least one line.';
      err.classList.remove('d-none');
      return;
    }
    const payload = {
      return_date: document.getElementById('return-date').value,
      refund_cash: document.getElementById('return-cash').value || 0,
      refund_credit: document.getElementById('return-credit').value || 0,
      notes: document.getElementById('return-notes').value || '',
      items,
    };
    const btn = document.getElementById('btn-save-return');
    btn.disabled = true;
    try {
      const res = await fetch('/sales/' + saleId + '/return', {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.ok) {
        err.textContent = data.error || 'Return failed';
        err.classList.remove('d-none');
        return;
      }
      bootstrap.Modal.getInstance(document.getElementById('returnSaleModal'))?.hide();
      window.location.reload();
    } catch (e2) {
      err.textContent = 'Network error. Try again.';
      err.classList.remove('d-none');
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById('btn-zero-stock-continue')?.addEventListener('click', () => {
    if (!pendingZeroStock) return;
    const pending = pendingZeroStock;
    pendingZeroStock = null;
    bootstrap.Modal.getInstance(document.getElementById('zeroStockWarningModal'))?.hide();
    document.getElementById('saleModal')?.classList.remove('modal-nested-open');

    if (pending.mode === 'save') {
      allowNegativeStock = true;
      const payload = {
        ...pending.payload,
        allow_negative_stock: true,
        items: (pending.payload.items || []).map((it) => ({
          ...it,
          allow_negative_stock: true,
        })),
      };
      submitSalePayload(payload, pending.url);
      return;
    }

    const { tr, product } = pending;
    if (tr && product) {
      fillProductOnRow(tr, { ...product, allow_negative: true });
      recalc();
    }
  });

  document.getElementById('btn-zero-stock-cancel')?.addEventListener('click', () => {
    pendingZeroStock = null;
    document.getElementById('saleModal')?.classList.remove('modal-nested-open');
  });

  document.getElementById('zeroStockWarningModal')?.addEventListener('hidden.bs.modal', () => {
    document.getElementById('saleModal')?.classList.remove('modal-nested-open');
  });

  document.getElementById('btn-zero-stock-add-product')?.addEventListener('click', () => {
    const pending = pendingZeroStock;
    const name =
      pending?.product?.name ||
      pending?.productName ||
      '';
    if (pending?.mode === 'select') {
      activeRow = pending.tr || activeRow;
    }
    pendingZeroStock = null;
    bootstrap.Modal.getInstance(document.getElementById('zeroStockWarningModal'))?.hide();
    openQuickProductModal(name);
  });
})();
