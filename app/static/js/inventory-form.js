(function () {
  if (!window.CategoryLookup) return;

  let productNameTimer = null;

  function clearSubcategory() {
    const subId = document.getElementById('subcategory_id');
    const subSearch = document.getElementById('subcat-search');
    if (subId) subId.value = '';
    if (subSearch) subSearch.value = '';
  }

  function clearExistingProduct() {
    const idEl = document.getElementById('existing_product_id');
    const hadExisting = Boolean(idEl?.value);
    if (idEl) idEl.value = '';
    const sku = document.getElementById('product-sku');
    if (sku) sku.readOnly = false;
    if (hadExisting) {
      fillSuggestedCodes({ force: true });
    }
  }

  async function refreshNextBatchPreview() {
    const batch = document.getElementById('product-batch');
    if (!batch) return;
    try {
      const res = await fetch('/api/products/next-codes');
      const data = await res.json();
      if (data.ok && data.next_batch_id != null) {
        batch.value = `#${data.next_batch_id}`;
      }
    } catch (_) {
      /* ignore */
    }
  }

  async function fillSuggestedCodes({ force = false } = {}) {
    const sku = document.getElementById('product-sku');
    const barcode = document.getElementById('product-barcode');
    const batch = document.getElementById('product-batch');
    if (!sku && !barcode && !batch) return;
    try {
      const res = await fetch('/api/products/next-codes');
      const data = await res.json();
      if (!data.ok) return;
      if (sku && (force || !sku.value.trim())) sku.value = data.sku || '';
      if (barcode && (force || !barcode.value.trim())) barcode.value = data.barcode || '';
      // Preview only — real batch id is created on save
      if (batch && data.next_batch_id != null) {
        batch.value = `#${data.next_batch_id}`;
      }
    } catch (_) {
      /* offline / ignore — server will still auto-fill on save */
    }
  }

  function fillProductFromLookup(p) {
    const idEl = document.getElementById('existing_product_id');
    if (idEl) idEl.value = p.id || '';

    const nameEl = document.getElementById('product-name-search');
    if (nameEl) nameEl.value = p.name || '';

    const sku = document.getElementById('product-sku');
    if (sku) {
      sku.value = p.sku || '';
      sku.readOnly = true;
    }
    const barcode = document.getElementById('product-barcode');
    if (barcode) barcode.value = p.barcode || '';
    const brand = document.getElementById('product-brand');
    if (brand) brand.value = p.brand || '';

    document.getElementById('category_id').value = p.category_id || '';
    document.getElementById('cat-search').value = p.category_name || '';
    document.getElementById('subcategory_id').value = p.subcategory_id || '';
    document.getElementById('subcat-search').value = p.subcategory_name || '';

    const purchase = document.getElementById('product-purchase');
    if (purchase) purchase.value = Number(p.purchase_price || 0).toFixed(2);
    const sale = document.getElementById('product-sale');
    if (sale) sale.value = Number(p.list_price != null ? p.list_price : p.sale_price || 0).toFixed(2);
    const min = document.getElementById('product-min');
    if (min) min.value = p.minimum_stock != null ? p.minimum_stock : 0;
    const uw = document.getElementById('product-unit-weight');
    if (uw) uw.value = p.unit_weight != null && p.unit_weight !== '' ? p.unit_weight : '';
    const wu = document.getElementById('product-weight-unit');
    if (wu) wu.value = p.weight_unit || '';
    const desc = document.getElementById('product-description');
    if (desc) desc.value = p.description || '';

    const picker = document.getElementById('inv-photo-picker');
    if (picker) {
      const pathEl = picker.querySelector('.photo-path');
      const clearFlag = picker.querySelector('.photo-clear-flag');
      if (pathEl) pathEl.value = p.photo || '';
      if (clearFlag) clearFlag.value = '';
      picker.dataset.current = p.photo_url || '';
      window.PhotoPicker?.setPreview?.(picker, p.photo_url || null);
    }
    refreshNextBatchPreview();
  }

  CategoryLookup.bind({
    searchId: 'cat-search',
    idId: 'category_id',
    resultsId: 'cat-results',
    label: 'category',
    onChange: clearSubcategory,
  });

  CategoryLookup.bind({
    searchId: 'subcat-search',
    idId: 'subcategory_id',
    resultsId: 'subcat-results',
    label: 'subcategory',
    requireParent: true,
    parentIdGetter: () => document.getElementById('category_id')?.value || '',
  });

  if (window.VendorLookup) {
    VendorLookup.bind({
      searchId: 'vendor-search',
      idId: 'vendor_id',
      resultsId: 'vendor-results',
      selectedId: 'vendor-selected',
      addBtnId: 'btn-add-vendor',
      modalId: 'quickVendorModal',
      required: true,
    });
  }

  const nameSearch = document.getElementById('product-name-search');
  const nameResults = document.getElementById('product-name-results');

  nameSearch?.addEventListener('input', () => {
    clearTimeout(productNameTimer);
    clearExistingProduct();
    const q = nameSearch.value.trim();
    if (q.length < 1) {
      nameResults?.classList.add('d-none');
      return;
    }
    productNameTimer = setTimeout(async () => {
      try {
        const res = await fetch('/api/products/lookup?q=' + encodeURIComponent(q));
        const data = await res.json();
        const rows = data.results || [];
        if (!nameResults) return;
        if (!rows.length) {
          nameResults.innerHTML =
            `<div class="p-2 small text-muted">No existing product — will create new</div>`;
        } else {
          nameResults.innerHTML = rows
            .map(
              (p) =>
                `<button type="button" class="lookup-item product-name-item"
                   data-id="${p.id}"
                   data-name="${(p.name || '').replace(/"/g, '&quot;')}"
                   data-sku="${(p.sku || '').replace(/"/g, '&quot;')}"
                   data-barcode="${(p.barcode || '').replace(/"/g, '&quot;')}"
                   data-brand="${(p.brand || '').replace(/"/g, '&quot;')}"
                   data-category-id="${p.category_id || ''}"
                   data-category-name="${(p.category_name || '').replace(/"/g, '&quot;')}"
                   data-subcategory-id="${p.subcategory_id || ''}"
                   data-subcategory-name="${(p.subcategory_name || '').replace(/"/g, '&quot;')}"
                   data-purchase="${p.purchase_price ?? 0}"
                   data-sale="${p.list_price ?? p.sale_price ?? 0}"
                   data-min="${p.minimum_stock ?? 0}"
                   data-desc="${(p.description || '').replace(/"/g, '&quot;')}"
                   data-photo="${(p.photo || '').replace(/"/g, '&quot;')}"
                   data-photo-url="${(p.photo_url || '').replace(/"/g, '&quot;')}">${p.label}</button>`
            )
            .join('');
        }
        nameResults.classList.remove('d-none');
      } catch (_) {
        if (nameResults) {
          nameResults.innerHTML = `<div class="p-2 text-danger small">Search failed</div>`;
          nameResults.classList.remove('d-none');
        }
      }
    }, 200);
  });

  nameResults?.addEventListener('click', (e) => {
    const btn = e.target.closest('.product-name-item');
    if (!btn) return;
    fillProductFromLookup({
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
      list_price: btn.dataset.sale,
      minimum_stock: btn.dataset.min,
      description: btn.dataset.desc,
      photo: btn.dataset.photo,
      photo_url: btn.dataset.photoUrl,
    });
    nameResults.classList.add('d-none');
  });

  const form = document.getElementById('entity-form');
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = document.getElementById('inv-form-error');
    if (err) {
      err.classList.add('d-none');
      err.textContent = '';
    }

    const cat = await CategoryLookup.ensureSelected('cat-search', 'category_id', null);
    if (!cat.ok) {
      if (err) {
        err.textContent = cat.error || 'Category is required';
        err.classList.remove('d-none');
      }
      return;
    }

    const subSearch = document.getElementById('subcat-search');
    const subName = subSearch?.value?.trim();
    if (subName) {
      const sub = await CategoryLookup.ensureSelected(
        'subcat-search',
        'subcategory_id',
        cat.id
      );
      if (!sub.ok) {
        if (err) {
          err.textContent = sub.error || 'Could not save subcategory';
          err.classList.remove('d-none');
        }
        return;
      }
    } else {
      const subId = document.getElementById('subcategory_id');
      if (subId) subId.value = '';
    }

    if (window.VendorLookup) {
      const vendor = VendorLookup.ensureSelected('vendor_id', true);
      if (!vendor.ok) {
        if (err) {
          err.textContent = vendor.error || 'Vendor is required';
          err.classList.remove('d-none');
        }
        return;
      }
    }

    const qty = Number(document.querySelector('[name="opening_stock"]')?.value || 0);
    if (!(qty > 0)) {
      if (err) {
        err.textContent = 'Purchase quantity is required.';
        err.classList.remove('d-none');
      }
      return;
    }

    form.submit();
  });

  document.getElementById('formModal')?.addEventListener('show.bs.modal', () => {
    const idEl = document.getElementById('existing_product_id');
    if (idEl) idEl.value = '';
    const sku = document.getElementById('product-sku');
    if (sku) sku.readOnly = false;
    nameResults?.classList.add('d-none');
    fillSuggestedCodes({ force: true });
  });

  document.addEventListener('click', (e) => {
    if (
      !e.target.closest('.lookup-results') &&
      !e.target.closest('#cat-search') &&
      !e.target.closest('#subcat-search') &&
      !e.target.closest('#vendor-search') &&
      !e.target.closest('#product-name-search')
    ) {
      document
        .querySelectorAll('#cat-results, #subcat-results, #vendor-results, #product-name-results')
        .forEach((el) => el.classList.add('d-none'));
    }
  });
})();
