(function () {
  const SESSION_KEY = 'erp-update-checked';
  const isDesktop = document.body.dataset.desktopApp === '1';
  const updatesEnabled = document.body.dataset.updatesEnabled === '1';

  function csrf() {
    return window.CSRF_TOKEN || document.querySelector('meta[name=csrf-token]')?.content || '';
  }

  function modalEl() {
    return document.getElementById('updateModal');
  }

  function showUpdateModal() {
    const el = modalEl();
    if (!el || !window.bootstrap) return;
    bootstrap.Modal.getOrCreateInstance(el).show();
  }

  function setModalState({ title, message, notes, mode, build }) {
    const titleEl = document.getElementById('update-modal-title');
    const msgEl = document.getElementById('update-modal-message');
    const notesWrap = document.getElementById('update-modal-notes-wrap');
    const notesEl = document.getElementById('update-modal-notes');
    const safeNote = document.getElementById('update-modal-safe-note');
    const applyBtn = document.getElementById('update-modal-apply');
    const skipBtn = document.getElementById('update-modal-skip');
    const okBtn = document.getElementById('update-modal-ok');
    const laterBtn = document.getElementById('update-modal-later');

    if (titleEl) titleEl.textContent = title || 'Updates';
    if (msgEl) msgEl.textContent = message || '';

    if (notes && notesEl && notesWrap) {
      notesEl.textContent = notes;
      notesWrap.classList.remove('d-none');
    } else if (notesWrap) {
      notesWrap.classList.add('d-none');
    }

    safeNote?.classList.toggle('d-none', mode !== 'available');
    applyBtn?.classList.toggle('d-none', mode !== 'available');
    skipBtn?.classList.toggle('d-none', mode !== 'available');
    okBtn?.classList.toggle('d-none', mode === 'available' || mode === 'loading');
    laterBtn?.classList.toggle('d-none', mode === 'offline' || mode === 'ok');

    if (skipBtn) skipBtn.dataset.build = build ? String(build) : '';
    if (applyBtn) applyBtn.disabled = false;
  }

  async function fetchUpdateStatus(respectSkip) {
    const res = await fetch(`/api/updates/check?respect_skip=${respectSkip ? '1' : '0'}`, {
      credentials: 'same-origin',
    });
    return res.json().catch(() => ({}));
  }

  async function checkUpdates({ auto = false, respectSkip = true } = {}) {
    if (!updatesEnabled) {
      if (!auto) {
        setModalState({
          title: 'Updates not configured',
          message: 'This copy does not have an update server URL yet.',
          mode: 'ok',
        });
        showUpdateModal();
      }
      return;
    }

    if (!auto) {
      setModalState({ title: 'Checking for updates...', message: 'Please wait.', mode: 'loading' });
      showUpdateModal();
    }

    try {
      const data = await fetchUpdateStatus(respectSkip);
      if (!data.ok && data.online === false) {
        setModalState({
          title: 'No internet connection',
          message: data.error || 'Connect to the internet to check for updates.',
          mode: 'offline',
        });
        if (auto && window.showOfflineModal) {
          window.showOfflineModal('Connect to the internet to check for app updates.');
        } else {
          showUpdateModal();
        }
        return;
      }

      if (!data.ok) {
        setModalState({
          title: 'Could not check updates',
          message: data.error || 'Try again later.',
          mode: 'ok',
        });
        if (!auto) showUpdateModal();
        return;
      }

      if (data.update_available) {
        setModalState({
          title: `Update available (v${data.latest_version})`,
          message: `You are on v${data.version}. A newer version is ready to install.`,
          notes: data.release_notes || '',
          mode: 'available',
          build: data.latest_build,
        });
        showUpdateModal();
        return;
      }

      if (!auto) {
        setModalState({
          title: 'You are up to date',
          message: `MBF ERP v${data.version} (build ${data.build}) is the latest version.`,
          mode: 'ok',
        });
        showUpdateModal();
      }
    } catch (_) {
      if (!auto) {
        setModalState({
          title: 'Could not check updates',
          message: 'Network error. Try again when internet is available.',
          mode: 'ok',
        });
        showUpdateModal();
      }
    }
  }

  async function applyUpdate() {
    const applyBtn = document.getElementById('update-modal-apply');
    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Downloading...';
    }
    document.getElementById('global-loader')?.classList.remove('d-none');

    try {
      const res = await fetch('/api/updates/apply', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf(),
        },
        credentials: 'same-origin',
        body: '{}',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'Update failed.');
      }

      setModalState({
        title: 'Update ready',
        message: data.message || 'The app will close and restart with the new version.',
        mode: 'ok',
      });
      showUpdateModal();

      setTimeout(async () => {
        if (window.pywebview?.api?.quit_for_update) {
          await window.pywebview.api.quit_for_update();
        } else {
          window.showErpToast?.(
            'warning',
            'Close the app completely, then run START_HERE.bat again to finish the update.'
          );
        }
      }, 1200);
    } catch (err) {
      document.getElementById('global-loader')?.classList.add('d-none');
      if (applyBtn) {
        applyBtn.disabled = false;
        applyBtn.innerHTML = '<i class="fa-solid fa-download me-1"></i>Update now';
      }
      window.showErpToast?.('danger', err.message || 'Could not download update.');
    }
  }

  async function skipVersion() {
    const skipBtn = document.getElementById('update-modal-skip');
    const build = parseInt(skipBtn?.dataset.build || '0', 10);
    if (!build) return;
    await fetch('/api/updates/skip', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf(),
      },
      body: JSON.stringify({ build }),
      credentials: 'same-origin',
    });
    bootstrap.Modal.getInstance(modalEl())?.hide();
  }

  function initUpdates() {
    if (!document.querySelector('.erp-wrapper')) return;

    document.getElementById('check-updates-btn')?.addEventListener('click', () => {
      checkUpdates({ auto: false, respectSkip: true });
    });
    document.getElementById('update-modal-apply')?.addEventListener('click', applyUpdate);
    document.getElementById('update-modal-skip')?.addEventListener('click', skipVersion);

    if (!isDesktop || !updatesEnabled) return;
    if (sessionStorage.getItem(SESSION_KEY) === '1') return;
    sessionStorage.setItem(SESSION_KEY, '1');
    setTimeout(() => checkUpdates({ auto: true, respectSkip: true }), 1500);
  }

  window.checkErpUpdates = checkUpdates;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUpdates);
  } else {
    initUpdates();
  }
})();
