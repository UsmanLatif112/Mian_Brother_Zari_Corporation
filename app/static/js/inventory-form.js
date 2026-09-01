(function () {
  if (!window.CategoryLookup) return;

  const linesEl = document.getElementById('inventory-lines');
  const form = document.getElementById('entity-form');
  const isSalesContext = Boolean(document.getElementById('quickProductModal'));
  const hostModalId = isSalesContext ? 'quickProductModal' : 'formModal';
  const saveUrl = isSalesContext ? '/api/products/batch' : '/inventory/products/create';
  let rowSeq = 0;
  let productNameTimer = null;
  /** Pending next batch seq per product id (or '_new') within this form. */
  const pendingBatchByKey = {};

  function csrf() {
    return window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;
  }

  function headers() {
    return {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf(),
    };
  }

  function showError(msg) {
    const err = document.getElementById('inv-form-error');
    if (!err) return;
    err.textContent = msg || '';
    err.classList.toggle('d-none', !msg);
  }

  function refreshRowNumbers() {
    const rows = linesEl?.querySelectorAll('.inv-line-row') || [];
    rows.forEach((row, i) => {
      const num = row.querySelector('.inv-line-num');
      if (num) num.textContent = String(i + 1);
      const removeBtn = row.querySelector('.btn-remove-inv-row');
      if (removeBtn) removeBtn.disabled = rows.length <= 1;
    });
  }

  function clearSubcategory(row) {
    const subId = row.querySelector('.inv-subcategory-id');
    const subSearch = row.querySelector('.inv-subcat-search');
    if (subId) subId.value = '';
    if (subSearch) subSearch.value = '';
  }

  function batchKeyForRow(row) {
    const existingId = row.querySelector('.inv-existing-id')?.value;
    return existingId ? `p:${existingId}` : `new:${row.dataset.uid || ''}`;
  }

  function refreshAllBatchPreviews() {
    Object.keys(pendingBatchByKey).forEach((k) => delete pendingBatchByKey[k]);
    const rows = linesEl?.querySelectorAll('.inv-line-row') || [];
    rows.forEach((row) => {
      const batch = row.querySelector('.inv-batch');
      if (!batch) return;
      const existingId = row.querySelector('.inv-existing-id')?.value;
      const base = Number(row.dataset.nextBatchBase || 1);
      const key = batchKeyForRow(row);
      if (pendingBatchByKey[key] == null) {
        pendingBatchByKey[key] = existingId ? base : 1;
      }
      batch.value = `#${pendingBatchByKey[key]}`;
      pendingBatchByKey[key] += 1;
    });
  }

  function clearExistingProduct(row) {
    const idEl = row.querySelector('.inv-existing-id');
    const hadExisting = Boolean(idEl?.value);
    if (idEl) idEl.value = '';
    const sku = row.querySelector('.inv-sku');
    if (sku) sku.readOnly = false;
    row.dataset.nextBatchBase = '1';
    if (hadExisting) {
      fillSuggestedCodes(row, { force: true });
    }
    refreshAllBatchPreviews();
  }

  function fillSuggestedCodes(row, { force = false } = {}) {
    const sku = row.querySelector('.inv-sku');
    const barcode = row.querySelector('.inv-barcode');
    if (sku && (force || !sku.value.trim())) sku.value = '';
    if (barcode && (force || !barcode.value.trim())) barcode.value = '';
    if (!row.dataset.nextBatchBase) row.dataset.nextBatchBase = '1';
    refreshAllBatchPreviews();
  }

  function fillProductFromLookup(row, p) {
    const idEl = row.querySelector('.inv-existing-id');
    if (idEl) idEl.value = p.id || '';

    const nameEl = row.querySelector('.inv-name-search');
    if (nameEl) nameEl.value = p.name || '';

    const sku = row.querySelector('.inv-sku');
    if (sku) {
      sku.value = p.sku || '';
      sku.readOnly = true;
    }
    const barcode = row.querySelector('.inv-barcode');
    if (barcode) barcode.value = p.barcode || '';
    const brand = row.querySelector('.inv-brand');
    if (brand) brand.value = p.brand || '';

    const catId = row.querySelector('.inv-category-id');
    const catSearch = row.querySelector('.inv-cat-search');
    if (catId) catId.value = p.category_id || '';
    if (catSearch) catSearch.value = p.category_name || '';

    const subId = row.querySelector('.inv-subcategory-id');
    const subSearch = row.querySelector('.inv-subcat-search');
    if (subId) subId.value = p.subcategory_id || '';
    if (subSearch) subSearch.value = p.subcategory_name || '';

    const purchase = row.querySelector('.inv-purchase');
    if (purchase) purchase.value = Number(p.purchase_price || 0).toFixed(2);
    const sale = row.querySelector('.inv-sale');
    if (sale) sale.value = Number(p.list_price != null ? p.list_price : p.sale_price || 0).toFixed(2);
    const min = row.querySelector('.inv-min');
    if (min) min.value = p.minimum_stock != null ? p.minimum_stock : 0;
    const uw = row.querySelector('.inv-unit-weight');
    if (uw) uw.value = p.unit_weight != null && p.unit_weight !== '' ? p.unit_weight : '';
    const wu = row.querySelector('.inv-weight-unit');
    if (wu) wu.value = p.weight_unit || 'kg';
    const desc = row.querySelector('.inv-description');
    if (desc) desc.value = p.description || '';

    row.dataset.nextBatchBase = String(p.next_batch || 1);

    const picker = row.querySelector('.photo-picker');
    if (picker) {
      const pathEl = picker.querySelector('.photo-path');
      const clearFlag = picker.querySelector('.photo-clear-flag');
      if (pathEl) pathEl.value = p.photo || '';
      if (clearFlag) clearFlag.value = '';
      picker.dataset.current = p.photo_url || '';
      window.PhotoPicker?.setPreview?.(picker, p.photo_url || null);
    }
    refreshAllBatchPreviews();
    syncLooseUnitLabel(row);
  }

  function bindProductNameLookup(row) {
    const nameSearch = row.querySelector('.inv-name-search');
    const nameResults = row.querySelector('.inv-name-results');
    if (!nameSearch || !nameResults) return;

    nameSearch.addEventListener('input', () => {
      clearTimeout(productNameTimer);
      clearExistingProduct(row);
      const q = nameSearch.value.trim();
      if (q.length < 1) {
        nameResults.classList.add('d-none');
        return;
      }
      productNameTimer = setTimeout(async () => {
        try {
          const res = await fetch('/api/products/lookup?q=' + encodeURIComponent(q));
          const data = await res.json();
          const rows = data.results || [];
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
                     data-unit-weight="${p.unit_weight ?? ''}"
                     data-weight-unit="${(p.weight_unit || '').replace(/"/g, '&quot;')}"
                     data-next-batch="${p.next_batch ?? 1}"
                     data-desc="${(p.description || '').replace(/"/g, '&quot;')}"
                     data-photo="${(p.photo || '').replace(/"/g, '&quot;')}"
                     data-photo-url="${(p.photo_url || '').replace(/"/g, '&quot;')}">${p.label}</button>`
              )
              .join('');
          }
          nameResults.classList.remove('d-none');
        } catch (_) {
          nameResults.innerHTML = `<div class="p-2 text-danger small">Search failed</div>`;
          nameResults.classList.remove('d-none');
        }
      }, 200);
    });

    nameResults.addEventListener('click', (e) => {
      const btn = e.target.closest('.product-name-item');
      if (!btn) return;
      fillProductFromLookup(row, {
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
        unit_weight: btn.dataset.unitWeight,
        weight_unit: btn.dataset.weightUnit,
        next_batch: Number(btn.dataset.nextBatch || 1),
        description: btn.dataset.desc,
        photo: btn.dataset.photo,
        photo_url: btn.dataset.photoUrl,
      });
      nameResults.classList.add('d-none');
    });
  }

  function bindCategoryLookups(row) {
    const catSearch = row.querySelector('.inv-cat-search');
    const catId = row.querySelector('.inv-category-id');
    const catResults = row.querySelector('.inv-cat-results');
    const subSearch = row.querySelector('.inv-subcat-search');
    const subId = row.querySelector('.inv-subcategory-id');
    const subResults = row.querySelector('.inv-subcat-results');

    CategoryLookup.bind({
      search: catSearch,
      idInput: catId,
      results: catResults,
      label: 'category',
      onChange: () => clearSubcategory(row),
    });

    CategoryLookup.bind({
      search: subSearch,
      idInput: subId,
      results: subResults,
      label: 'subcategory',
      requireParent: true,
      parentIdGetter: () => catId?.value || '',
    });
  }

  function weightUnitLabel(row) {
    const wu = row.querySelector('.inv-weight-unit')?.value || 'kg';
    return wu || 'kg';
  }

  function syncLooseUnitLabel(row) {
    const unit = weightUnitLabel(row);
    row.querySelectorAll('.inv-loose-unit').forEach((el) => {
      el.textContent = unit;
    });
  }

  function bindWeightFields(row) {
    const uwEl = row.querySelector('.inv-unit-weight');
    const wuEl = row.querySelector('.inv-weight-unit');
    uwEl?.addEventListener('input', () => {
      if (Number(uwEl.value || 0) > 0 && wuEl && !wuEl.value) {
        wuEl.value = 'kg';
      }
      syncLooseUnitLabel(row);
    });
    wuEl?.addEventListener('change', () => syncLooseUnitLabel(row));
    syncLooseUnitLabel(row);
  }

  function rowTemplate() {
    rowSeq += 1;
    const uid = rowSeq;
    const wrap = document.createElement('div');
    wrap.className = 'inv-line-row border rounded p-2 mb-2';
    wrap.dataset.uid = String(uid);
    wrap.innerHTML = `
      <div class="d-flex justify-content-between align-items-center mb-2">
        <span class="small text-muted fw-semibold">Product <span class="inv-line-num">1</span></span>
        <button type="button" class="btn btn-sm btn-light border-0 btn-remove-inv-row" title="Remove row">
          <i class="fa-solid fa-trash-can text-danger"></i>
        </button>
      </div>
      <div class="row g-2 align-items-end">
        <div class="col-auto">
          <div class="photo-picker photo-picker-line photo-picker-form" data-icon="fa-camera" data-aspect="1"
               data-upload="ajax" data-folder="products" title="Product photo (optional)">
            <input type="file" accept="image/*" class="d-none photo-file-input">
            <input type="hidden" class="photo-path" value="">
            <input type="hidden" value="" class="photo-clear-flag">
            <div class="line-photo-frame" role="button" tabindex="0" aria-label="Product photo">
              <div class="photo-preview line-photo-preview" aria-hidden="true"><i class="fa-solid fa-camera"></i></div>
              <span class="line-photo-hint">Add</span>
              <button type="button" class="line-photo-clear photo-clear" title="Remove photo" aria-label="Remove photo">
                <i class="fa-solid fa-xmark"></i>
              </button>
            </div>
          </div>
        </div>
        <div class="col-md-5">
          <label class="form-label">Product Name</label>
          <div class="position-relative">
            <input type="text" class="form-control inv-name-search" autocomplete="off" placeholder="Search existing or type new name…">
            <input type="hidden" class="inv-existing-id" value="">
            <div class="lookup-results d-none inv-name-results"></div>
          </div>
        </div>
        <div class="col-md-3">
          <label class="form-label">SKU</label>
          <input type="text" class="form-control inv-sku" placeholder="Auto on save">
        </div>
        <div class="col-md-3">
          <label class="form-label">Barcode</label>
          <input type="text" class="form-control inv-barcode" placeholder="Auto on save">
        </div>

        <div class="col-md-4">
          <label class="form-label">Category</label>
          <div class="position-relative">
            <input type="text" class="form-control inv-cat-search" placeholder="Type category…" autocomplete="off">
            <input type="hidden" class="inv-category-id" value="">
            <div class="lookup-results d-none inv-cat-results"></div>
          </div>
        </div>
        <div class="col-md-4">
          <label class="form-label">Subcategory <span class="text-muted fw-normal">(optional)</span></label>
          <div class="position-relative">
            <input type="text" class="form-control inv-subcat-search" placeholder="Type subcategory…" autocomplete="off">
            <input type="hidden" class="inv-subcategory-id" value="">
            <div class="lookup-results d-none inv-subcat-results"></div>
          </div>
        </div>
        <div class="col-md-4">
          <label class="form-label">Brand</label>
          <input type="text" class="form-control inv-brand" placeholder="Optional">
        </div>

        <div class="col-md-2">
          <label class="form-label">Full Item <span class="text-danger">*</span></label>
          <input type="number" step="1" min="0" class="form-control inv-sealed-bags" value="">
        </div>
        <div class="col-md-3">
          <label class="form-label">Loose Item</label>
          <div class="input-group">
            <input type="number" step="0.001" min="0" class="form-control inv-loose-weight" value="0">
            <span class="input-group-text inv-loose-unit">kg</span>
          </div>
        </div>
        <div class="col-md-3">
          <label class="form-label">Unit Weight</label>
          <div class="input-group">
            <input type="number" step="0.001" min="0" class="form-control inv-unit-weight" placeholder="50">
            <select class="form-select inv-weight-unit" style="max-width:5.5rem">
              <option value="kg">kg</option>
              <option value="g">g</option>
              <option value="L">L</option>
              <option value="ml">ml</option>
            </select>
          </div>
        </div>
        <div class="col-md-2">
          <label class="form-label">Purchase Price</label>
          <input type="number" step="0.01" min="0" class="form-control inv-purchase" value="0">
        </div>
        <div class="col-md-2">
          <label class="form-label">Sale Price</label>
          <input type="number" step="0.01" min="0" class="form-control inv-sale" value="0">
        </div>
        <div class="col-md-2">
          <label class="form-label">Batch</label>
          <input type="text" class="form-control bg-light inv-batch" readonly tabindex="-1" placeholder="#1">
        </div>
        <div class="col-md-2">
          <label class="form-label">Expiry Date</label>
          <input type="date" class="form-control inv-expiry">
        </div>
        <div class="col-12">
          <label class="form-label">Description</label>
          <textarea class="form-control inv-description" rows="2"></textarea>
        </div>
      </div>
    `;
    return wrap;
  }

  function addRow() {
    if (!linesEl) return;
    const row = rowTemplate();
    row.dataset.nextBatchBase = '1';
    linesEl.appendChild(row);
    bindCategoryLookups(row);
    bindProductNameLookup(row);
    bindWeightFields(row);
    window.PhotoPicker?.bindAll?.(row);
    fillSuggestedCodes(row, { force: true });
    row.querySelector('.btn-remove-inv-row')?.addEventListener('click', () => {
      const rows = linesEl.querySelectorAll('.inv-line-row');
      if (rows.length <= 1) return;
      row.remove();
      refreshRowNumbers();
      refreshAllBatchPreviews();
    });
    refreshRowNumbers();
  }

  async function collectItems() {
    const items = [];
    const rows = linesEl?.querySelectorAll('.inv-line-row') || [];
    for (const row of rows) {
      const name = row.querySelector('.inv-name-search')?.value?.trim() || '';
      if (!name) {
        return { ok: false, error: 'Enter a product name on every row.' };
      }

      const cat = await CategoryLookup.ensureSelected(
        row.querySelector('.inv-cat-search'),
        row.querySelector('.inv-category-id'),
        null
      );
      if (!cat.ok) {
        return { ok: false, error: cat.error || `Category is required for “${name}”.` };
      }

      const subSearch = row.querySelector('.inv-subcat-search');
      const subName = subSearch?.value?.trim();
      if (subName) {
        const sub = await CategoryLookup.ensureSelected(
          subSearch,
          row.querySelector('.inv-subcategory-id'),
          cat.id
        );
        if (!sub.ok) {
          return { ok: false, error: sub.error || `Could not save subcategory for “${name}”.` };
        }
      } else {
        const subId = row.querySelector('.inv-subcategory-id');
        if (subId) subId.value = '';
      }

      const uw = Number(row.querySelector('.inv-unit-weight')?.value || 0);
      const sealedBags = Number(row.querySelector('.inv-sealed-bags')?.value || 0);
      const looseKg = Number(row.querySelector('.inv-loose-weight')?.value || 0);

      if (looseKg > 0 && !(uw > 0)) {
        return {
          ok: false,
          error: `Set unit weight for “${name}” when entering loose item.`,
        };
      }

      let openingStock = 0;
      if (uw > 0) {
        openingStock = sealedBags + looseKg / uw;
      } else {
        openingStock = sealedBags;
      }
      if (!(openingStock > 0)) {
        return {
          ok: false,
          error: `Enter full item and/or loose item for “${name}”.`,
        };
      }

      const lineItem = {
        name,
        existing_product_id: row.querySelector('.inv-existing-id')?.value || '',
        sku: row.querySelector('.inv-sku')?.value?.trim() || '',
        barcode: row.querySelector('.inv-barcode')?.value?.trim() || '',
        brand: row.querySelector('.inv-brand')?.value?.trim() || '',
        category_id: row.querySelector('.inv-category-id')?.value || '',
        subcategory_id: row.querySelector('.inv-subcategory-id')?.value || '',
        purchase_price: row.querySelector('.inv-purchase')?.value || 0,
        sale_price: row.querySelector('.inv-sale')?.value || 0,
        opening_stock: openingStock,
        minimum_stock: row.querySelector('.inv-min')?.value || 0,
        unit_weight: row.querySelector('.inv-unit-weight')?.value || '',
        weight_unit: row.querySelector('.inv-weight-unit')?.value || '',
        batch_number: '',
        expiry_date: row.querySelector('.inv-expiry')?.value || '',
        description: row.querySelector('.inv-description')?.value?.trim() || '',
        photo: row.querySelector('.photo-path')?.value || '',
        sealed_bags: sealedBags,
        loose_weight_kg: looseKg,
      };
      items.push(lineItem);
    }
    if (!items.length) {
      return { ok: false, error: 'Add at least one product row.' };
    }
    return { ok: true, items };
  }

  function resetForm() {
    showError('');
    if (linesEl) linesEl.innerHTML = '';
    Object.keys(pendingBatchByKey).forEach((k) => delete pendingBatchByKey[k]);
    const vendorSearch = document.getElementById('vendor-search');
    const vendorId = document.getElementById('vendor_id');
    const invoiceNo = document.getElementById('invoice_no');
    if (vendorSearch) vendorSearch.value = '';
    if (vendorId) vendorId.value = '';
    if (invoiceNo) invoiceNo.value = '';
    addRow();
    const pendingName = window._pendingSaleProductName;
    if (pendingName) {
      const firstRow = linesEl?.querySelector('.inv-line-row');
      const nameEl = firstRow?.querySelector('.inv-name-search');
      if (nameEl) nameEl.value = pendingName;
      window._pendingSaleProductName = '';
    }
  }

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

  document.getElementById('btn-add-inv-row')?.addEventListener('click', () => addRow());

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    showError('');

    if (window.VendorLookup) {
      const vendor = VendorLookup.ensureSelected('vendor_id', true);
      if (!vendor.ok) {
        showError(vendor.error || 'Vendor is required');
        return;
      }
    } else if (!document.getElementById('vendor_id')?.value) {
      showError('Vendor is required');
      return;
    }

    const collected = await collectItems();
    if (!collected.ok) {
      showError(collected.error);
      return;
    }

    const payload = {
      vendor_id: document.getElementById('vendor_id')?.value || '',
      invoice_no: document.getElementById('invoice_no')?.value?.trim() || '',
      items: collected.items,
    };

    const submitBtn = document.querySelector('button[type="submit"][form="entity-form"]');
    if (submitBtn) submitBtn.disabled = true;

    try {
      const res = await fetch(saveUrl, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.ok) {
        showError(data.error || 'Could not save products');
        return;
      }
      if (isSalesContext && typeof window.onInventoryProductSaved === 'function') {
        window.onInventoryProductSaved(data);
        return;
      }
      window.location.href = data.redirect || '/inventory/';
    } catch (_) {
      showError('Network error. Try again.');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  document.getElementById(hostModalId)?.addEventListener('show.bs.modal', () => {
    resetForm();
  });

  document.addEventListener('click', (e) => {
    if (
      !e.target.closest('.lookup-results') &&
      !e.target.closest('.inv-cat-search') &&
      !e.target.closest('.inv-subcat-search') &&
      !e.target.closest('#vendor-search') &&
      !e.target.closest('.inv-name-search')
    ) {
      document
        .querySelectorAll(
          '.inv-cat-results, .inv-subcat-results, #vendor-results, .inv-name-results'
        )
        .forEach((el) => el.classList.add('d-none'));
    }
  });

  // Nested vendor modal stacking
  document.getElementById('quickVendorModal')?.addEventListener('show.bs.modal', () => {
    document.getElementById(hostModalId)?.classList.add('modal-nested-open');
  });
  document.getElementById('quickVendorModal')?.addEventListener('hidden.bs.modal', () => {
    document.getElementById(hostModalId)?.classList.remove('modal-nested-open');
  });
})();
