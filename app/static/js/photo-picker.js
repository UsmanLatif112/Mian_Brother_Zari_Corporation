/**
 * Photo studio: small placeholder → modal (upload / camera) → crop → apply.
 */
(function (global) {
  let cameraStream = null;
  let cropper = null;
  let activeWrap = null;
  let objectUrl = null;
  let studioModal = null;
  let parentModalId = null;

  function rememberParentModal() {
    const open = document.querySelector('.modal.show:not(#photoStudioModal)');
    parentModalId = open?.id || null;
    open?.classList.add('modal-nested-open');
  }

  function restoreParentModal() {
    document.querySelectorAll('.modal.modal-nested-open').forEach((m) => m.classList.remove('modal-nested-open'));
    if (!parentModalId) return;
    const parent = document.getElementById(parentModalId);
    parentModalId = null;
    if (parent && !parent.classList.contains('show')) {
      bootstrap.Modal.getOrCreateInstance(parent).show();
    }
  }

  function setPreview(wrap, url) {
    const preview = wrap.querySelector('.photo-preview');
    const frame = wrap.querySelector('.line-photo-frame') || wrap.querySelector('.photo-thumb');
    if (!preview) return;
    if (url) {
      preview.style.backgroundImage = `url("${url}")`;
      preview.classList.add('has-photo');
      wrap.classList.add('has-photo');
      frame?.classList.add('has-photo');
      preview.innerHTML = '';
    } else {
      preview.style.backgroundImage = '';
      preview.classList.remove('has-photo');
      wrap.classList.remove('has-photo');
      frame?.classList.remove('has-photo');
      const icon = wrap.dataset.icon || 'fa-user';
      preview.innerHTML = `<i class="fa-solid ${icon}"></i>`;
    }
    const hint = wrap.querySelector('.line-photo-hint') || wrap.querySelector('.photo-thumb-hint');
    if (hint) hint.textContent = url ? 'Change' : 'Add';
  }

  function destroyCropper() {
    if (cropper) {
      cropper.destroy();
      cropper = null;
    }
  }

  function revokeObjectUrl() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
  }

  function stopCamera() {
    if (cameraStream) {
      cameraStream.getTracks().forEach((t) => t.stop());
      cameraStream = null;
    }
    const video = document.getElementById('photo-studio-video');
    if (video) video.srcObject = null;
  }

  function ensureStudioModal() {
    let el = document.getElementById('photoStudioModal');
    if (el) return el;

    el = document.createElement('div');
    el.id = 'photoStudioModal';
    el.className = 'modal fade erp-form-modal photo-studio-modal';
    el.tabIndex = -1;
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = `
      <div class="modal-dialog modal-dialog-centered modal-lg">
        <div class="modal-content photo-studio-content">
          <div class="modal-header border-0 pb-0">
            <div>
              <h5 class="modal-title mb-0">Photo</h5>
              <div class="small text-muted" id="photo-studio-subtitle">Choose a source, then crop</div>
            </div>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <!-- Step: source -->
            <div id="photo-studio-source" class="photo-studio-pane">
              <div class="photo-studio-source-grid">
                <button type="button" class="photo-studio-source-card" id="photo-studio-upload">
                  <span class="photo-studio-source-icon"><i class="fa-solid fa-images"></i></span>
                  <span class="photo-studio-source-label">Upload</span>
                  <span class="photo-studio-source-hint">Gallery or files</span>
                </button>
                <button type="button" class="photo-studio-source-card" id="photo-studio-camera">
                  <span class="photo-studio-source-icon photo-studio-source-icon-cam"><i class="fa-solid fa-camera"></i></span>
                  <span class="photo-studio-source-label">Camera</span>
                  <span class="photo-studio-source-hint">Take a live photo</span>
                </button>
              </div>
              <input type="file" accept="image/*" class="d-none" id="photo-studio-file">
            </div>

            <!-- Step: live camera -->
            <div id="photo-studio-camera-pane" class="photo-studio-pane d-none">
              <div class="photo-studio-camera-wrap">
                <video id="photo-studio-video" autoplay playsinline muted></video>
                <div class="text-danger small mt-2 d-none" id="photo-studio-cam-error"></div>
              </div>
            </div>

            <!-- Step: crop -->
            <div id="photo-studio-crop" class="photo-studio-pane d-none">
              <div class="photo-studio-crop-wrap">
                <img id="photo-studio-crop-img" alt="Crop preview">
              </div>
              <div class="photo-studio-toolbar">
                <button type="button" class="btn btn-sm btn-light border" id="photo-studio-zoom-out" title="Zoom out">
                  <i class="fa-solid fa-magnifying-glass-minus"></i>
                </button>
                <button type="button" class="btn btn-sm btn-light border" id="photo-studio-zoom-in" title="Zoom in">
                  <i class="fa-solid fa-magnifying-glass-plus"></i>
                </button>
                <button type="button" class="btn btn-sm btn-light border" id="photo-studio-rotate" title="Rotate">
                  <i class="fa-solid fa-rotate-right"></i>
                </button>
                <button type="button" class="btn btn-sm btn-light border" id="photo-studio-reset" title="Reset">
                  <i class="fa-solid fa-arrow-rotate-left"></i>
                </button>
                <span class="photo-studio-toolbar-hint">Drag to reframe · scroll to zoom</span>
              </div>
            </div>
          </div>
          <div class="modal-footer border-0 pt-0">
            <button type="button" class="btn btn-outline-secondary" id="photo-studio-back" hidden>Back</button>
            <div class="ms-auto d-flex gap-2">
              <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
              <button type="button" class="btn btn-success d-none" id="photo-studio-capture">
                <i class="fa-solid fa-camera me-1"></i> Capture
              </button>
              <button type="button" class="btn btn-success d-none" id="photo-studio-apply">
                <i class="fa-solid fa-check me-1"></i> Apply
              </button>
            </div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(el);

    const fileInput = el.querySelector('#photo-studio-file');
    el.querySelector('#photo-studio-upload').addEventListener('click', () => {
      fileInput.value = '';
      fileInput.click();
    });
    fileInput.addEventListener('change', () => {
      const file = fileInput.files?.[0];
      if (file) openCrop(file);
    });
    el.querySelector('#photo-studio-camera').addEventListener('click', () => startCamera());
    el.querySelector('#photo-studio-capture').addEventListener('click', () => captureFromCamera());
    el.querySelector('#photo-studio-apply').addEventListener('click', () => applyCrop());
    el.querySelector('#photo-studio-back').addEventListener('click', () => goBack());
    el.querySelector('#photo-studio-zoom-in').addEventListener('click', () => cropper?.zoom(0.1));
    el.querySelector('#photo-studio-zoom-out').addEventListener('click', () => cropper?.zoom(-0.1));
    el.querySelector('#photo-studio-rotate').addEventListener('click', () => cropper?.rotate(90));
    el.querySelector('#photo-studio-reset').addEventListener('click', () => cropper?.reset());

    el.addEventListener('show.bs.modal', () => {
      rememberParentModal();
    });

    el.addEventListener('hidden.bs.modal', () => {
      stopCamera();
      destroyCropper();
      revokeObjectUrl();
      activeWrap = null;
      showPane('source');
      restoreParentModal();
      if (document.querySelector('.modal.show')) {
        document.body.classList.add('modal-open');
      }
    });

    return el;
  }

  function showPane(name) {
    const el = ensureStudioModal();
    const source = el.querySelector('#photo-studio-source');
    const cam = el.querySelector('#photo-studio-camera-pane');
    const crop = el.querySelector('#photo-studio-crop');
    const back = el.querySelector('#photo-studio-back');
    const capture = el.querySelector('#photo-studio-capture');
    const apply = el.querySelector('#photo-studio-apply');
    const sub = el.querySelector('#photo-studio-subtitle');

    source.classList.toggle('d-none', name !== 'source');
    cam.classList.toggle('d-none', name !== 'camera');
    crop.classList.toggle('d-none', name !== 'crop');

    capture.classList.toggle('d-none', name !== 'camera');
    apply.classList.toggle('d-none', name !== 'crop');
    back.hidden = name === 'source';

    if (name === 'source') {
      sub.textContent = 'Choose a source, then crop';
      stopCamera();
      destroyCropper();
      revokeObjectUrl();
    } else if (name === 'camera') {
      sub.textContent = 'Position the subject, then capture';
    } else if (name === 'crop') {
      sub.textContent = 'Adjust framing, then apply';
      stopCamera();
    }
  }

  function goBack() {
    const cropPane = document.getElementById('photo-studio-crop');
    const camPane = document.getElementById('photo-studio-camera-pane');
    if (cropPane && !cropPane.classList.contains('d-none')) {
      destroyCropper();
      revokeObjectUrl();
      showPane('source');
      return;
    }
    if (camPane && !camPane.classList.contains('d-none')) {
      stopCamera();
      showPane('source');
    }
  }

  function openStudio(wrap) {
    activeWrap = wrap;
    const el = ensureStudioModal();
    showPane('source');
    studioModal = bootstrap.Modal.getOrCreateInstance(el);
    studioModal.show();
  }

  async function startCamera() {
    const video = document.getElementById('photo-studio-video');
    const errEl = document.getElementById('photo-studio-cam-error');
    if (errEl) {
      errEl.classList.add('d-none');
      errEl.textContent = '';
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      openNativeCameraFallback();
      return;
    }

    try {
      stopCamera();
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
        audio: false,
      });
      video.srcObject = cameraStream;
      await video.play();
      showPane('camera');
    } catch (_) {
      openNativeCameraFallback();
    }
  }

  function openNativeCameraFallback() {
    const wrap = activeWrap;
    if (!wrap) return;
    let camInput = wrap.querySelector('.photo-camera-input');
    if (!camInput) {
      camInput = document.createElement('input');
      camInput.type = 'file';
      camInput.accept = 'image/*';
      camInput.capture = 'environment';
      camInput.className = 'd-none photo-camera-input';
      wrap.appendChild(camInput);
      camInput.addEventListener('change', () => {
        const file = camInput.files?.[0];
        if (file) openCrop(file);
        camInput.value = '';
      });
    }
    camInput.setAttribute('capture', 'environment');
    camInput.click();
  }

  function captureFromCamera() {
    const video = document.getElementById('photo-studio-video');
    if (!video?.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      if (!blob) return;
      const file = new File([blob], `camera-${Date.now()}.jpg`, { type: 'image/jpeg' });
      openCrop(file);
    }, 'image/jpeg', 0.92);
  }

  function openCrop(file) {
    if (!file || !file.type.startsWith('image/')) {
      alert('Please choose an image file');
      return;
    }
    destroyCropper();
    revokeObjectUrl();
    objectUrl = URL.createObjectURL(file);
    const img = document.getElementById('photo-studio-crop-img');
    img.onload = () => {
      showPane('crop');
      const ratio = Number(activeWrap?.dataset.aspect || 1) || 1;
      if (typeof Cropper === 'undefined') {
        alert('Crop tool failed to load. Please refresh the page.');
        return;
      }
      cropper = new Cropper(img, {
        aspectRatio: ratio,
        viewMode: 1,
        dragMode: 'move',
        autoCropArea: 0.9,
        responsive: true,
        background: false,
        guides: true,
        center: true,
        highlight: false,
        cropBoxMovable: true,
        cropBoxResizable: true,
        toggleDragModeOnDblclick: false,
      });
    };
    img.src = objectUrl;
  }

  function applyCrop() {
    if (!cropper || !activeWrap) return;
    const wrap = activeWrap;
    const canvas = cropper.getCroppedCanvas({
      width: 800,
      height: 800,
      imageSmoothingEnabled: true,
      imageSmoothingQuality: 'high',
    });
    if (!canvas) return;

    const applyBtn = document.getElementById('photo-studio-apply');
    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Saving…';
    }

    canvas.toBlob(async (blob) => {
      try {
        if (!blob) throw new Error('Crop failed');
        const file = new File([blob], `photo-${Date.now()}.jpg`, { type: 'image/jpeg' });
        await applyFile(wrap, file);
        studioModal?.hide();
      } catch (_) {
        alert('Could not save photo');
      } finally {
        if (applyBtn) {
          applyBtn.disabled = false;
          applyBtn.innerHTML = '<i class="fa-solid fa-check me-1"></i> Apply';
        }
      }
    }, 'image/jpeg', 0.9);
  }

  async function applyFile(wrap, file) {
    const input = wrap.querySelector('.photo-file-input') || wrap.querySelector('input[type="file"]:not(.photo-camera-input)');
    const pathEl = wrap.querySelector('.photo-path');
    const clearFlag = wrap.querySelector('.photo-clear-flag');
    if (clearFlag) clearFlag.value = '';

    if (input && typeof DataTransfer !== 'undefined') {
      try {
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
      } catch (_) {
        /* some browsers block setting .files */
      }
    }

    const localUrl = URL.createObjectURL(file);
    setPreview(wrap, localUrl);

    if (wrap.dataset.upload === 'ajax') {
      const folder = wrap.dataset.folder || 'sales';
      const fd = new FormData();
      fd.append('photo', file);
      fd.append('folder', folder);
      try {
        const res = await fetch('/api/uploads/photo', {
          method: 'POST',
          headers: { 'X-CSRFToken': global.CSRF_TOKEN || '' },
          body: fd,
        });
        const data = await res.json();
        if (!data.ok) {
          alert(data.error || 'Upload failed');
          setPreview(wrap, wrap.dataset.current || null);
          if (input) input.value = '';
          if (pathEl) pathEl.value = '';
          return;
        }
        if (pathEl) pathEl.value = data.path;
        setPreview(wrap, data.url);
        wrap.dataset.current = data.url;
      } catch (_) {
        alert('Upload failed');
        setPreview(wrap, wrap.dataset.current || null);
        if (input) input.value = '';
      }
    }
  }

  function bindPicker(wrap) {
    if (!wrap || wrap.dataset.bound === '1') return;
    wrap.dataset.bound = '1';

    const clearBtn = wrap.querySelector('.photo-clear');
    const pathEl = wrap.querySelector('.photo-path');
    const clearFlag = wrap.querySelector('.photo-clear-flag');
    const input = wrap.querySelector('.photo-file-input') || wrap.querySelector('input[type="file"]:not(.photo-camera-input)');
    const openBtn = wrap.querySelector('.photo-open-btn');
    const current = wrap.dataset.current || '';

    if (current) setPreview(wrap, current);
    else setPreview(wrap, null);

    const open = (e) => {
      e?.preventDefault();
      e?.stopPropagation();
      openStudio(wrap);
    };

    openBtn?.addEventListener('click', open);
    wrap.querySelector('.photo-preview')?.addEventListener('click', open);
    wrap.querySelector('.line-photo-frame')?.addEventListener('click', (e) => {
      if (e.target.closest('.photo-clear')) return;
      open(e);
    });
    wrap.querySelector('.line-photo-frame')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') open(e);
    });
    wrap.querySelector('.photo-thumb')?.addEventListener('click', (e) => {
      if (e.target.closest('.photo-clear')) return;
      open(e);
    });
    wrap.querySelector('.photo-thumb')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') open(e);
    });

    // Legacy buttons still open the studio
    wrap.querySelector('.photo-upload-btn')?.addEventListener('click', open);
    wrap.querySelector('.photo-camera-btn')?.addEventListener('click', open);

    clearBtn?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (input) input.value = '';
      if (pathEl) pathEl.value = '';
      wrap.dataset.current = '';
      if (clearFlag) clearFlag.value = '1';
      setPreview(wrap, null);
    });
  }

  function bindAll(root) {
    (root || document).querySelectorAll('.photo-picker').forEach(bindPicker);
  }

  global.PhotoPicker = { bind: bindPicker, bindAll, setPreview, open: openStudio };
  document.addEventListener('DOMContentLoaded', () => bindAll());
})(window);
