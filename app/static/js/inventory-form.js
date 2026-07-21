(function () {
  if (!window.CategoryLookup) return;

  function clearSubcategory() {
    const subId = document.getElementById('subcategory_id');
    const subSearch = document.getElementById('subcat-search');
    if (subId) subId.value = '';
    if (subSearch) subSearch.value = '';
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

  document.addEventListener('click', (e) => {
    if (
      !e.target.closest('.lookup-results') &&
      !e.target.closest('#cat-search') &&
      !e.target.closest('#subcat-search') &&
      !e.target.closest('#vendor-search')
    ) {
      document
        .querySelectorAll('#cat-results, #subcat-results, #vendor-results')
        .forEach((el) => el.classList.add('d-none'));
    }
  });
})();
