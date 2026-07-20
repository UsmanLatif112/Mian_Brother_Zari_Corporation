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

    form.submit();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.lookup-results') && !e.target.closest('#cat-search') && !e.target.closest('#subcat-search')) {
      document.querySelectorAll('#cat-results, #subcat-results').forEach((el) => el.classList.add('d-none'));
    }
  });
})();
