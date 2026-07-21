(function () {
  const csrf = () => window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;
  const money = (n) => Number(n || 0).toFixed(2);
  const tbody = document.querySelector('#sale-lines tbody');
  let activeRow = null;
  let customerTimer = null;
  let productTimer = null;

  function headers() {
    return {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf(),
    };
  }

  function rowTemplate() {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="line-photo-td">
        <div class="photo-picker photo-picker-line" data-icon="fa-camera" data-upload="ajax" data-folder="sales" data-aspect="1" title="Add item photo">
          <input type="file" accept="image/*" class="d-none photo-file-input">
          <input type="hidden" class="photo-path" value="">
          <div class="line-photo-frame" role="button" tabindex="0" aria-label="Add photo">
            <div class="photo-preview line-photo-preview" aria-hidden="true">
              <i class="fa-solid fa-camera"></i>
            </div>
            <span class="line-photo-hint">Add</span>
            <button type="button" class="line-photo-clear photo-clear" title="Remove photo" aria-label="Remove photo">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
        </div>
      </td>
      <td>
        <div class="position-relative">
          <div class="input-group input-group-sm erp-input-group-quick sale-item-search">
            <input type="text" class="form-control product-search" placeholder="Search item..." autocomplete="off">
            <button type="button" class="btn erp-btn-quick-add btn-quick-product" title="Add product"><i class="fa-solid fa-plus"></i></button>
          </div>
          <input type="hidden" class="product-id" value="">
          <input type="hidden" class="list-price" value="0">
          <div class="lookup-results d-none product-results"></div>
          <div class="small text-muted list-price-hint d-none"></div>
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

  function setListPriceHint(tr, listPrice) {
    const hint = tr.querySelector('.list-price-hint');
    const listEl = tr.querySelector('.list-price');
    if (listEl) listEl.value = money(listPrice);
    if (!hint) return;
    if (listPrice > 0) {
      hint.textContent = 'List: ' + money(listPrice);
      hint.classList.remove('d-none');
    } else {
      hint.classList.add('d-none');
    }
  }

  function updatePaymentUI() {
    const status = document.getElementById('payment-status')?.value || 'paid';
    const grand = grandTotal();
    const paidEl = document.getElementById('amount-paid');
    const remainWrap = document.getElementById('remain-wrap');
    const advanceWrap = document.getElementById('advance-wrap');
    const paidWrap = document.getElementById('amount-paid-wrap');

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
      const current = Number(paidEl.value || 0);
      if (current <= grand) paidEl.value = money(grand);
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
      tr.querySelector('.product-id').value = prefill.id;
      tr.querySelector('.product-search').value = prefill.name;
      tr.querySelector('.price').value = money(prefill.sale_price);
      setListPriceHint(tr, prefill.sale_price);
    }
    bindRow(tr);
    const picker = tr.querySelector('.photo-picker');
    if (picker && window.PhotoPicker) window.PhotoPicker.bind(picker);
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
      bootstrap.Modal.getOrCreateInstance(document.getElementById('quickProductModal')).show();
    });

    const search = tr.querySelector('.product-search');
    const results = tr.querySelector('.product-results');
    search?.addEventListener('input', () => {
      clearTimeout(productTimer);
      const q = search.value.trim();
      tr.querySelector('.product-id').value = '';
      if (q.length < 1) {
        results.classList.add('d-none');
        return;
      }
      productTimer = setTimeout(async () => {
        const res = await fetch('/api/products/lookup?q=' + encodeURIComponent(q));
        const data = await res.json();
        if (!data.results.length) {
          results.innerHTML = `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add "${q}"</button>`;
        } else {
          results.innerHTML = data.results
            .map(
              (p) =>
                `<button type="button" class="lookup-item" data-id="${p.id}" data-name="${p.name}" data-price="${p.sale_price}">${p.label}</button>`
            )
            .join('') +
            `<button type="button" class="lookup-item lookup-create" data-name="${q}">+ Add new product</button>`;
        }
        results.classList.remove('d-none');
      }, 200);
    });

    results?.addEventListener('click', (e) => {
      const btn = e.target.closest('.lookup-item');
      if (!btn) return;
      if (btn.classList.contains('lookup-create')) {
        activeRow = tr;
        document.getElementById('qp-name').value = btn.dataset.name || '';
        results.classList.add('d-none');
        bootstrap.Modal.getOrCreateInstance(document.getElementById('quickProductModal')).show();
        return;
      }
      tr.querySelector('.product-id').value = btn.dataset.id;
      tr.querySelector('.product-search').value = btn.dataset.name;
      tr.querySelector('.price').value = money(btn.dataset.price);
      setListPriceHint(tr, btn.dataset.price);
      results.classList.add('d-none');
      recalc();
    });
  }

  document.getElementById('btn-add-row')?.addEventListener('click', () => addRow());

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
      // Suggest half if empty / full
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
      setListPriceHint(activeRow, data.sale_price);
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
        photo: tr.querySelector('.photo-path')?.value || null,
      });
    });
    if (!items.length) {
      err.textContent = 'Add at least one product line.';
      err.classList.remove('d-none');
      return;
    }
    const payload = {
      sale_date: document.getElementById('sale-date').value,
      customer_id: document.getElementById('customer-id').value || null,
      payment_status: document.getElementById('payment-status').value,
      amount_paid: document.getElementById('amount-paid').value,
      discount: document.getElementById('sale-discount')?.value || 0,
      notes: document.getElementById('sale-notes').value,
      items,
    };
    const btn = document.getElementById('btn-save-sale');
    btn.disabled = true;
    try {
      const res = await fetch('/sales/create', {
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

  // Nested modals: keep sale modal visible
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

  // Init
  if (tbody && !tbody.rows.length) addRow();
  if (window.OPEN_SALE_MODAL) {
    bootstrap.Modal.getOrCreateInstance(document.getElementById('saleModal')).show();
  }
  // Drop stale ?open_modal= from URL so refresh does not reopen the modal
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
