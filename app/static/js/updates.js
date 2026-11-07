(function () {
  // localStorage is shared across desktop child windows (sessionStorage is not).
  const SESSION_KEY = 'erp-update-checked';
  const OFFLINE_NOTICE_KEY = 'erp-offline-update-notice';
  const UPDATE_SHOWN_KEY = 'erp-update-available-shown';
  const isDesktop = document.body.dataset.desktopApp === '1';
  const updatesEnabled = document.body.dataset.updatesEnabled === '1';

  function storageGet(key) {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return sessionStorage.getItem(key);
    }
  }

  function storageSet(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (_) {
      sessionStorage.setItem(key, value);
    }
  }

  function isChildAppWindow() {
    try {
      return new URLSearchParams(window.location.search).get('mbf_child') === '1';
    } catch (_) {
      return false;
    }
  }

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

  function setProgressVisible(show) {
    document.getElementById('update-modal-progress-wrap')?.classList.toggle('d-none', !show);
  }

  function setProgress({ percent, label, detail, indeterminate }) {
    const wrap = document.getElementById('update-modal-progress-wrap');
    const bar = document.getElementById('update-modal-progress-bar');
    const pctEl = document.getElementById('update-modal-progress-pct');
    const labelEl = document.getElementById('update-modal-progress-label');
    const detailEl = document.getElementById('update-modal-progress-detail');
    if (!wrap || !bar) return;

    wrap.classList.remove('d-none');
    const pct = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
    bar.style.width = `${pct}%`;
    bar.setAttribute('aria-valuenow', String(pct));
    bar.classList.toggle('progress-bar-animated', !!indeterminate || pct < 100);
    bar.classList.toggle('progress-bar-striped', !!indeterminate || pct < 100);
    if (pctEl) pctEl.textContent = `${pct}%`;
    if (labelEl && label) labelEl.textContent = label;
    if (detailEl) detailEl.textContent = detail || '';
  }

  function setDownloadingUi(active) {
    const el = modalEl();
    const closeBtn = document.getElementById('update-modal-close');
    const laterBtn = document.getElementById('update-modal-later');
    const skipBtn = document.getElementById('update-modal-skip');
    const applyBtn = document.getElementById('update-modal-apply');
    const okBtn = document.getElementById('update-modal-ok');
    if (el && window.bootstrap) {
      const instance = bootstrap.Modal.getOrCreateInstance(el, {
        backdrop: active ? 'static' : true,
        keyboard: !active,
      });
      instance._config.backdrop = active ? 'static' : true;
      instance._config.keyboard = !active;
    }
    if (closeBtn) {
      closeBtn.disabled = !!active;
      closeBtn.classList.toggle('d-none', !!active);
    }
    if (laterBtn) {
      laterBtn.disabled = !!active;
      laterBtn.classList.toggle('d-none', !!active);
    }
    if (skipBtn) skipBtn.classList.add('d-none');
    if (okBtn) okBtn.classList.add('d-none');
    if (applyBtn) {
      applyBtn.disabled = !!active;
      applyBtn.classList.toggle('d-none', !!active);
    }
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
    const closeBtn = document.getElementById('update-modal-close');

    if (titleEl) titleEl.textContent = title || 'Updates';
    if (msgEl) msgEl.textContent = message || '';

    if (notes && notesEl && notesWrap) {
      notesEl.textContent = notes;
      notesWrap.classList.remove('d-none');
    } else if (notesWrap) {
      notesWrap.classList.add('d-none');
    }

    if (mode !== 'downloading') {
      setProgressVisible(false);
      if (closeBtn) closeBtn.disabled = false;
      if (laterBtn) laterBtn.disabled = false;
    }

    safeNote?.classList.toggle('d-none', mode !== 'available');
    applyBtn?.classList.toggle('d-none', mode !== 'available');
    skipBtn?.classList.toggle('d-none', mode !== 'available');
    okBtn?.classList.toggle('d-none', mode === 'available' || mode === 'loading' || mode === 'downloading');
    laterBtn?.classList.toggle('d-none', mode === 'offline' || mode === 'ok' || mode === 'downloading');

    if (skipBtn) skipBtn.dataset.build = build ? String(build) : '';
    if (applyBtn) {
      applyBtn.disabled = false;
      applyBtn.innerHTML = '<i class="fa-solid fa-download me-1"></i>Update now';
    }
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
      // Only show "No Internet" when the PC is actually offline
      if (!data.ok && data.online === false) {
        setModalState({
          title: 'No internet connection',
          message: data.error || 'Connect to the internet to check for updates.',
          mode: 'offline',
        });
        if (auto) {
          // Once per app session across all windows — "OK, continue offline".
          if (storageGet(OFFLINE_NOTICE_KEY) === '1') return;
          if (window.showOfflineModal) {
            window.showOfflineModal('Connect to the internet to check for app updates.');
          }
        } else {
          showUpdateModal();
        }
        return;
      }

      // Online but update server / version.json missing or failed — not "no internet"
      if (!data.ok) {
        setModalState({
          title: 'Could not check updates',
          message: data.error || 'Update server is not available yet. You can keep using the app.',
          mode: 'ok',
        });
        // Auto-check: stay quiet when online but no published release
        if (!auto) showUpdateModal();
        return;
      }

      if (data.update_available) {
        const buildKey = String(data.latest_build || data.latest_version || '');
        if (auto && buildKey && storageGet(UPDATE_SHOWN_KEY) === buildKey) {
          return;
        }
        setModalState({
          title: `Update available (v${data.latest_version})`,
          message: `You are on v${data.version}. A newer version is ready to install.`,
          notes: data.release_notes || '',
          mode: 'available',
          build: data.latest_build,
        });
        if (auto && buildKey) storageSet(UPDATE_SHOWN_KEY, buildKey);
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

  function phaseLabel(phase) {
    switch (phase) {
      case 'check':
        return 'Checking…';
      case 'download':
        return 'Downloading…';
      case 'verify':
        return 'Verifying…';
      case 'extract':
        return 'Extracting…';
      case 'prepare':
        return 'Preparing…';
      case 'done':
        return 'Complete';
      default:
        return 'Updating…';
    }
  }

  async function readNdjsonProgress(res, onEvent) {
    if (!res.body || !res.body.getReader) {
      const data = await res.json().catch(() => ({}));
      onEvent(data);
      return data;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let last = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const evt = JSON.parse(trimmed);
          last = evt;
          onEvent(evt);
        } catch (_) {
          /* ignore partial/bad line */
        }
      }
    }

    const tail = buffer.trim();
    if (tail) {
      try {
        const evt = JSON.parse(tail);
        last = evt;
        onEvent(evt);
      } catch (_) {
        /* ignore */
      }
    }
    return last || {};
  }

  async function applyUpdate() {
    const applyBtn = document.getElementById('update-modal-apply');
    setModalState({
      title: 'Downloading update',
      message: 'Please keep this window open until the download finishes.',
      mode: 'downloading',
    });
    setDownloadingUi(true);
    setProgress({
      percent: 2,
      label: 'Starting…',
      detail: 'Connecting to update server…',
      indeterminate: true,
    });
    showUpdateModal();

    try {
      const res = await fetch('/api/updates/apply', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/x-ndjson, application/json',
          'X-CSRFToken': csrf(),
        },
        credentials: 'same-origin',
        body: '{}',
      });

      if (!res.ok && !res.body) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Update failed.');
      }

      const finalEvent = await readNdjsonProgress(res, (evt) => {
        if (evt.phase === 'error' || evt.ok === false) {
          return;
        }
        setProgress({
          percent: evt.percent ?? 0,
          label: phaseLabel(evt.phase),
          detail: evt.message || '',
          indeterminate: (evt.percent || 0) < 5,
        });
        const msgEl = document.getElementById('update-modal-message');
        if (msgEl && evt.message) msgEl.textContent = evt.message;
      });

      if (!finalEvent || finalEvent.ok === false || finalEvent.phase === 'error') {
        throw new Error((finalEvent && finalEvent.error) || 'Update failed.');
      }

      setProgress({
        percent: 100,
        label: 'Complete',
        detail: finalEvent.message || 'Update ready.',
        indeterminate: false,
      });
      setModalState({
        title: 'Update ready',
        message: finalEvent.message || 'The app will close and restart with the new version.',
        mode: 'ok',
      });
      setProgressVisible(true);
      setProgress({
        percent: 100,
        label: 'Complete',
        detail: finalEvent.message || '',
        indeterminate: false,
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
      setDownloadingUi(false);
      setProgressVisible(false);
      setModalState({
        title: 'Update failed',
        message: err.message || 'Could not download update.',
        mode: 'ok',
      });
      if (applyBtn) {
        applyBtn.disabled = false;
        applyBtn.innerHTML = '<i class="fa-solid fa-download me-1"></i>Update now';
        applyBtn.classList.remove('d-none');
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

    document.getElementById('offlineModal')?.addEventListener('hidden.bs.modal', () => {
      storageSet(OFFLINE_NOTICE_KEY, '1');
    });

    if (!isDesktop || !updatesEnabled) return;
    // Child page windows skip auto check — main window owns update/offline notice.
    if (isChildAppWindow()) return;
    if (storageGet(SESSION_KEY) === '1') return;
    storageSet(SESSION_KEY, '1');
    setTimeout(() => checkUpdates({ auto: true, respectSkip: true }), 1500);
  }

  window.checkErpUpdates = checkUpdates;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUpdates);
  } else {
    initUpdates();
  }
})();
