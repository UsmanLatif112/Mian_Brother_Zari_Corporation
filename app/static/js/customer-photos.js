(function (global) {
  function csrf() {
    return global.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content;
  }

  function parseExisting(field) {
    try {
      const raw = field.dataset.existing || '[]';
      return JSON.parse(raw) || [];
    } catch (_) {
      return [];
    }
  }

  function thumbHtml({ url, id, path, isNew }) {
    const removeAttrs = id
      ? `data-photo-id="${id}" data-remove-type="existing"`
      : `data-photo-path="${path || ''}" data-remove-type="new"`;
    return `
      <div class="customer-photo-thumb" ${removeAttrs}>
        <img src="${url}" alt="">
        <button type="button" class="customer-photo-remove" title="Remove photo" aria-label="Remove photo">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>`;
  }

  function renderField(field, photos) {
    const grid = field.querySelector('.customer-photos-grid');
    const newPaths = field.querySelector('.customer-photos-new-paths');
    const removeIds = field.querySelector('.customer-photos-remove-ids');
    if (!grid || !newPaths || !removeIds) return;

    grid.innerHTML = '';
    newPaths.innerHTML = '';
    removeIds.innerHTML = '';

    (photos || []).forEach((p) => {
      if (!p?.url) return;
      grid.insertAdjacentHTML('beforeend', thumbHtml({ url: p.url, id: p.id, isNew: false }));
    });
  }

  function addNewPhoto(field, url, path) {
    const grid = field.querySelector('.customer-photos-grid');
    const newPaths = field.querySelector('.customer-photos-new-paths');
    if (!grid || !newPaths || !path) return;

    grid.insertAdjacentHTML('beforeend', thumbHtml({ url, path, isNew: true }));
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'new_photo_paths';
    input.value = path;
    newPaths.appendChild(input);
  }

  function removeThumb(field, thumb) {
    const removeType = thumb.dataset.removeType;
    if (removeType === 'existing' && thumb.dataset.photoId) {
      const removeIds = field.querySelector('.customer-photos-remove-ids');
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'remove_photo_id';
      input.value = thumb.dataset.photoId;
      removeIds?.appendChild(input);
    } else if (removeType === 'new') {
      const path = thumb.dataset.photoPath;
      field.querySelectorAll('.customer-photos-new-paths input').forEach((inp) => {
        if (inp.value === path) inp.remove();
      });
    }
    thumb.remove();
  }

  function bindField(field) {
    if (!field || field.dataset.bound === '1') return;
    field.dataset.bound = '1';

    renderField(field, parseExisting(field));

    field.querySelector('.btn-add-customer-photo')?.addEventListener('click', () => {
      let picker = field.querySelector('.customer-photo-add-picker');
      if (!picker) {
        picker = document.createElement('div');
        picker.className =
          'photo-picker photo-picker-line photo-picker-form customer-photo-add-picker d-none';
        picker.dataset.upload = 'ajax';
        picker.dataset.folder = field.dataset.folder || 'customers';
        picker.dataset.icon = field.dataset.icon || 'fa-user';
        picker.innerHTML = `
          <input type="file" accept="image/*" class="d-none photo-file-input">
          <input type="hidden" class="photo-path" value="">
          <input type="hidden" value="" class="photo-clear-flag">
          <div class="line-photo-frame" role="button" tabindex="0" aria-label="Add customer photo">
            <div class="photo-preview line-photo-preview" aria-hidden="true"><i class="fa-solid fa-camera"></i></div>
          </div>`;
        field.appendChild(picker);
        global.PhotoPicker?.bind?.(picker);
      }

      const pathEl = picker.querySelector('.photo-path');
      const clearFlag = picker.querySelector('.photo-clear-flag');
      if (pathEl) pathEl.value = '';
      if (clearFlag) clearFlag.value = '';
      picker.dataset.current = '';

      const onApplied = (e) => {
        if (e.detail?.wrap !== picker) return;
        const path = picker.querySelector('.photo-path')?.value;
        const url = picker.dataset.current;
        if (path && url) addNewPhoto(field, url, path);
        document.removeEventListener('photo-picker-applied', onApplied);
      };
      document.addEventListener('photo-picker-applied', onApplied);
      global.PhotoPicker?.open?.(picker);
    });

    field.querySelector('.customer-photos-grid')?.addEventListener('click', (e) => {
      const btn = e.target.closest('.customer-photo-remove');
      if (!btn) return;
      const thumb = btn.closest('.customer-photo-thumb');
      if (thumb) removeThumb(field, thumb);
    });
  }

  function bindAll(root) {
    (root || document).querySelectorAll('.customer-photos-field').forEach(bindField);
  }

  function loadExisting(form, photos) {
    const field = form?.querySelector('.customer-photos-field');
    if (!field) return;
    field.dataset.existing = JSON.stringify(photos || []);
    renderField(field, photos || []);
  }

  function resetField(root) {
    const field = (root || document)?.querySelector?.('.customer-photos-field');
    if (!field) return;
    field.dataset.existing = '[]';
    renderField(field, []);
  }

  function collectNewPaths(root) {
    const field = (root || document)?.querySelector?.('.customer-photos-field');
    if (!field) return [];
    return Array.from(field.querySelectorAll('.customer-photos-new-paths input'))
      .map((inp) => inp.value)
      .filter(Boolean);
  }

  global.CustomerPhotos = { bindAll, loadExisting, resetField, collectNewPaths };
  document.addEventListener('DOMContentLoaded', () => bindAll());

  document.getElementById('formModal')?.addEventListener('show.bs.modal', () => {
    const field = document.querySelector('#entity-form .customer-photos-field');
    if (field) {
      field.dataset.existing = '[]';
      renderField(field, []);
    }
  });
})(window);
