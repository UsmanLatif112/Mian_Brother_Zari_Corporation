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
          <div class="lookup-results d-none product-results"></div>
        </div>
      </td>
      <td><input type="number" min="0.001" step="0.001" class="form-control form-control-sm qty" value="1"></td>
      <td><input type="number" min="0" step="0.01" class="form-control form-control-sm price" value="0" title="Editable — auto-filled from item sale price"></td>
      <td class="text-end line-total fw-semibold">0.00</td>
      <td class="text-end">
        <button type="button" class="btn btn-sm btn-light border-0 sale-row-remove btn-remove-row" title="Remove row">
          <i class="fa-solid fa-trash-can text-danger"></i>
        </button>
      </td>
    `;
    return tr;
  }

  function subtotal() {
    let sum = 0;
    tbody.querySelectorAll('tr').forEach((tr) => {
      const qty = Number(tr.querySelector('.qty')?.value || 0);
      const price = Number(tr.querySelector('.price')?.value || 0);
      sum += qty * price;
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
    paidEl.readOnly = false;

    if (status === 'paid') {
      // Paid: settle bill at minimum; keep higher amount as overpay/advance
      const cur = Number(paidEl.value || 0);
      if (cur < grand - 0.001) paidEl.value = money(grand);
    }

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
      const qty = Number(tr.querySelector('.qty')?.value || 0);
      const price = Number(tr.querySelector('.price')?.value || 0);
      const line = qty * price;
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
      tr.querySelector('.product-id').value = prefill.id || prefill.product_id || '';
      tr.querySelector('.product-search').value = prefill.name || '';
      tr.querySelector('.price').value = money(prefill.sale_price ?? prefill.unit_price ?? 0);
      if (prefill.quantity != null) tr.querySelector('.qty').value = prefill.quantity;
      setProductThumb(tr, prefill.photo_url || null);
    }
    bindRow(tr);
    recalc();
    return tr;
  }

  function bindRow(tr) {
    tr.querySelector('.qty')?.addEventListener('input', recalc);
    tr.querySelector('.price')?.addEventListener('input', recalc);
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
                .map(
                  (p) =>
                    `<button type="button" class="lookup-item" data-id="${p.id}" data-name="${p.name}" data-price="${p.sale_price}" data-photo="${p.photo_url || ''}">${p.label}</button>`
                )
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
      tr.querySelector('.product-id').value = btn.dataset.id;
      tr.querySelector('.product-search').value = btn.dataset.name;
      tr.querySelector('.price').value = money(btn.dataset.price);
      setProductThumb(tr, btn.dataset.photo || null);
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
      document.getElementById('sale-date').dataset.today || new Date().toISOString().slice(0, 10);
    document.getElementById('customer-id').value = '';
    document.getElementById('customer-search').value = '';
    document.getElementById('customer-selected').textContent = 'Walk-in';
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
      document.getElementById('payment-status').value = s.payment_status || 'paid';
      document.getElementById('sale-discount').value = money(s.discount || 0);
      document.getElementById('sale-notes').value = s.notes || '';

      tbody.innerHTML = '';
      (s.items || []).forEach((it) => addRow(it));
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

  document.getElementById('qp-save')?.addEventListener('click', async () => {
    const err = document.getElementById('qp-error');
    err.classList.add('d-none');

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
      sale_price: document.getElementById('qp-price').value,
      purchase_price: document.getElementById('qp-purchase').value,
      opening_stock: document.getElementById('qp-stock').value,
      minimum_stock: document.getElementById('qp-min-stock')?.value || 0,
      batch_number: document.getElementById('qp-batch')?.value.trim() || '',
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
      activeRow.querySelector('.product-id').value = data.id;
      activeRow.querySelector('.product-search').value = data.name;
      activeRow.querySelector('.price').value = money(data.sale_price);
      setProductThumb(activeRow, data.photo_url || null);
      activeRow.querySelector('.product-results')?.classList.add('d-none');
      recalc();
    }
    bootstrap.Modal.getInstance(document.getElementById('quickProductModal'))?.hide();
  });

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
      items.push({
        product_id: Number(pid),
        quantity: Number(tr.querySelector('.qty')?.value || 0),
        unit_price: Number(tr.querySelector('.price')?.value || 0),
      });
    });
    if (!items.length) {
      err.textContent = 'Add at least one product line.';
      err.classList.remove('d-none');
      return;
    }
    const paymentStatus = document.getElementById('payment-status').value;
    const customerId = document.getElementById('customer-id').value || null;
    if ((paymentStatus === 'unpaid' || paymentStatus === 'partial') && !customerId) {
      err.textContent = 'Select a customer for credit or partial sales.';
      err.classList.remove('d-none');
      return;
    }
    const payload = {
      sale_date: document.getElementById('sale-date').value,
      customer_id: customerId,
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
      window.location.href = data.invoice_url || '/sales/' + data.sale_id + '/invoice';
    } catch (e) {
      err.textContent = 'Network error. Try again.';
      err.classList.remove('d-none');
    } finally {
      btn.disabled = false;
    }
  });

  ['quickCustomerModal', 'quickProductModal'].forEach((id) => {
    const el = document.getElementById(id);
    el?.addEventListener('show.bs.modal', () => {
      document.getElementById('saleModal')?.classList.add('modal-nested-open');
    });
    el?.addEventListener('hidden.bs.modal', () => {
      const saleModal = document.getElementById('saleModal');
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
