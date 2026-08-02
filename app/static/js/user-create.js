(function () {
  const CHECK_URL = '/auth/users/check';
  let checkTimer = null;

  function setFieldState(input, { valid, message, hint }) {
    if (!input) return;
    const wrap = input.closest('.field-check-wrap') || input.parentElement;
    const feedback = wrap?.querySelector('.field-check-feedback');
    const hintEl = wrap?.querySelector('.field-check-hint');
    input.classList.remove('is-valid', 'is-invalid');
    if (valid === true) input.classList.add('is-valid');
    if (valid === false) input.classList.add('is-invalid');
    if (feedback) {
      feedback.textContent = message || '';
      feedback.classList.toggle('d-none', !message);
      feedback.classList.toggle('text-danger', valid === false);
      feedback.classList.toggle('text-success', valid === true);
    }
    if (hintEl) {
      hintEl.textContent = hint || '';
      hintEl.classList.toggle('d-none', !hint);
      hintEl.classList.toggle('text-warning', Boolean(hint));
    }
  }

  async function checkAvailability(username, email) {
    const params = new URLSearchParams();
    if (username) params.set('username', username);
    if (email) params.set('email', email);
    if (!params.toString()) return null;
    const res = await fetch(`${CHECK_URL}?${params.toString()}`, {
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
    if (!res.ok) return null;
    return res.json();
  }

  function scheduleAvailabilityCheck(form) {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(async () => {
      const usernameInput = form.querySelector('[name="username"]');
      const emailInput = form.querySelector('[name="email"]');
      const username = (usernameInput?.value || '').trim();
      const email = (emailInput?.value || '').trim();
      if (!username && !email) return;
      try {
        const data = await checkAvailability(username, email);
        if (!data) return;
        if (username && data.username) {
          setFieldState(usernameInput, {
            valid: data.username.available ? true : false,
            message: data.username.available ? null : data.username.message,
            hint: data.username.restore ? data.username.message : null,
          });
        }
        if (email && data.email) {
          setFieldState(emailInput, {
            valid: data.email.available ? true : false,
            message: data.email.available ? null : data.email.message,
            hint: data.email.restore ? data.email.message : null,
          });
        }
      } catch (_) { /* ignore */ }
    }, 350);
  }

  function resetCreateForm(form) {
    form.reset();
    form.querySelectorAll('.is-valid, .is-invalid').forEach((el) => {
      el.classList.remove('is-valid', 'is-invalid');
    });
    form.querySelectorAll('.field-check-feedback, .field-check-hint').forEach((el) => {
      el.textContent = '';
      el.classList.add('d-none');
    });
    const checklist = form.querySelector('.password-policy-checklist');
    if (checklist) checklist.innerHTML = '';
    form.querySelectorAll('.photo-path').forEach((el) => { el.value = ''; });
    form.querySelectorAll('.photo-clear-flag').forEach((el) => { el.value = ''; });
    form.querySelectorAll('.photo-preview').forEach((el) => {
      el.innerHTML = '<i class="fa-solid fa-camera"></i>';
      el.style.backgroundImage = '';
    });
    form.querySelectorAll('.photo-picker').forEach((el) => el.classList.remove('has-photo'));
  }

  function initCreateUserForm() {
    const form = document.getElementById('entity-form');
    if (!form || form.dataset.ajaxUserCreate !== '1') return;

    const usernameInput = form.querySelector('[name="username"]');
    const emailInput = form.querySelector('[name="email"]');
    const submitBtn = document.querySelector('button[form="entity-form"]');

    [usernameInput, emailInput].forEach((input) => {
      input?.addEventListener('input', () => scheduleAvailabilityCheck(form));
      input?.addEventListener('blur', () => scheduleAvailabilityCheck(form));
    });

    const modalEl = document.getElementById('formModal');
    modalEl?.addEventListener('hidden.bs.modal', () => resetCreateForm(form));

    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const passwordInput = form.querySelector('[data-password-main]');
      const pwd = passwordInput?.value || '';
      const pwdErrors = window.ErpPasswordValidation?.policyErrors(pwd, { required: true }) || [];
      if (pwdErrors.length) {
        window.ErpPasswordValidation?.setFieldError(passwordInput, pwdErrors[0]);
        window.showErpToast?.('danger', pwdErrors[0]);
        return;
      }

      const username = (usernameInput?.value || '').trim();
      const email = (emailInput?.value || '').trim();
      if (!username) {
        setFieldState(usernameInput, { valid: false, message: 'Username is required.' });
        window.showErpToast?.('danger', 'Username is required.');
        return;
      }

      try {
        const availability = await checkAvailability(username, email);
        if (availability?.username && !availability.username.available) {
          setFieldState(usernameInput, { valid: false, message: availability.username.message });
          window.showErpToast?.('danger', availability.username.message);
          return;
        }
        if (availability?.email && !availability.email.available) {
          setFieldState(emailInput, { valid: false, message: availability.email.message });
          window.showErpToast?.('danger', availability.email.message);
          return;
        }
      } catch (_) {
        window.showErpToast?.('danger', 'Could not verify username. Try again.');
        return;
      }

      if (submitBtn) submitBtn.disabled = true;
      try {
        const formData = new FormData(form);
        const res = await fetch(form.action, {
          method: 'POST',
          headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': window.CSRF_TOKEN || '',
          },
          body: formData,
          credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          const msg = data.error || data.message || 'Could not create user.';
          const field = data.field;
          if (field === 'username') setFieldState(usernameInput, { valid: false, message: msg });
          if (field === 'email') setFieldState(emailInput, { valid: false, message: msg });
          window.showErpToast?.('danger', msg);
          return;
        }
        window.showErpToast?.(data.warning ? 'warning' : 'success', data.message || 'User created.');
        const modal = modalEl && window.bootstrap ? bootstrap.Modal.getInstance(modalEl) : null;
        modal?.hide();
        if (data.package_url) {
          const frame = document.createElement('iframe');
          frame.style.display = 'none';
          frame.src = data.package_url;
          document.body.appendChild(frame);
          window.showErpToast?.(
            'info',
            `Downloading ${data.package_filename || 'data package'}… Send this zip with the app to the user.`
          );
        }
        setTimeout(() => {
          window.location.href = data.redirect || window.location.pathname;
        }, data.package_url ? 1400 : 600);
      } catch (_) {
        window.showErpToast?.('danger', 'Could not create user. Try again.');
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCreateUserForm);
  } else {
    initCreateUserForm();
  }
})();
