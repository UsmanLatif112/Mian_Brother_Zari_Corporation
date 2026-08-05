(function () {
  const RULES = [
    { id: 'length', test: (p) => p.length >= 8, label: 'At least 8 characters' },
    { id: 'upper', test: (p) => /[A-Z]/.test(p), label: 'One uppercase letter' },
    { id: 'number', test: (p) => /\d/.test(p), label: 'One number' },
    { id: 'special', test: (p) => /[^\w\s]/.test(p), label: 'One special character' },
  ];

  function policyErrors(password, { required = true } = {}) {
    const pwd = (password || '').trim();
    if (!pwd) {
      return required ? ['Password is required.'] : [];
    }
    const errors = [];
    RULES.forEach((rule) => {
      if (!rule.test(pwd)) errors.push(rule.label + '.');
    });
    return errors;
  }

  function setFieldError(input, message) {
    if (!input) return;
    input.classList.toggle('is-invalid', Boolean(message));
    const host = input.closest('.password-field-wrap') || input.closest('.mb-3');
    const feedback = host?.querySelector('.invalid-feedback');
    if (feedback) {
      feedback.textContent = message || '';
      feedback.classList.toggle('d-none', !message);
    }
  }

  function renderChecklist(container, password) {
    if (!container) return;
    const pwd = password || '';
    container.innerHTML = RULES.map((rule) => {
      const ok = rule.test(pwd);
      const icon = ok ? 'fa-circle-check text-success' : 'fa-circle-xmark text-danger';
      return `<li class="small ${ok ? 'text-success' : 'text-muted'}"><i class="fa-solid ${icon} me-1"></i>${rule.label}</li>`;
    }).join('');
  }

  function bindPasswordField(wrap, { required = true, confirmInput = null } = {}) {
    const input = wrap?.querySelector('.password-strength-input');
    const checklist = wrap?.querySelector('.password-policy-checklist');
    if (!input) return;

    const validate = () => {
      const errors = policyErrors(input.value, { required });
      setFieldError(input, errors[0] || '');
      renderChecklist(checklist, input.value);
      if (confirmInput) {
        const confirmVal = confirmInput.value.trim();
        if (confirmVal && confirmVal !== input.value.trim()) {
          setFieldError(confirmInput, 'Passwords do not match.');
        } else if (confirmInput.classList.contains('is-invalid') && confirmVal === input.value.trim()) {
          setFieldError(confirmInput, '');
        }
      }
      return errors;
    };

    input.addEventListener('input', validate);
    input.addEventListener('blur', validate);
    if (confirmInput) {
      confirmInput.addEventListener('input', () => {
        const confirmVal = confirmInput.value.trim();
        const pwd = input.value.trim();
        if (!confirmVal) {
          setFieldError(confirmInput, '');
          return;
        }
        setFieldError(confirmInput, confirmVal === pwd ? '' : 'Passwords do not match.');
      });
    }
    validate();
  }

  function validatePasswordPair(password, confirm, { required = true, checkConfirm = false } = {}) {
    const errors = policyErrors(password, { required });
    if (checkConfirm && (confirm || '').trim() !== (password || '').trim()) {
      errors.push('Passwords do not match.');
    }
    return errors;
  }

  async function ajaxValidatePassword(password, confirm) {
    try {
      const res = await fetch('/auth/validate-password', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': window.CSRF_TOKEN || '',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({ password, confirm }),
        credentials: 'same-origin',
      });
      return await res.json();
    } catch (_) {
      return { ok: false, errors: ['Could not validate password. Try again.'] };
    }
  }

  function initPasswordToggles(root) {
    (root || document).querySelectorAll('.password-toggle-btn').forEach((btn) => {
      if (btn.dataset.boundToggle) return;
      btn.dataset.boundToggle = '1';
      btn.addEventListener('click', () => {
        const input = btn.closest('.input-group')?.querySelector('input');
        if (!input) return;
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        const icon = btn.querySelector('i');
        if (icon) icon.className = show ? 'fa-regular fa-eye-slash' : 'fa-regular fa-eye';
        btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      });
    });
  }

  function initPasswordForms() {
    initPasswordToggles();
    document.querySelectorAll('[data-password-form]').forEach((form) => {
      const required = form.dataset.passwordRequired !== '0';
      const passwordInput = form.querySelector('[data-password-main]');
      const confirmInput = form.querySelector('[data-password-confirm]');
      const wrap = passwordInput?.closest('.password-field-wrap');
      if (wrap) bindPasswordField(wrap, { required, confirmInput });
      if (confirmInput) {
        const confirmWrap = confirmInput.closest('.password-field-wrap');
        if (confirmWrap && !confirmWrap.querySelector('.password-strength-input')) {
          confirmInput.addEventListener('input', () => {
            const confirmVal = confirmInput.value.trim();
            const pwd = (passwordInput?.value || '').trim();
            if (!confirmVal) {
              setFieldError(confirmInput, '');
              return;
            }
            setFieldError(confirmInput, confirmVal === pwd ? '' : 'Passwords do not match.');
          });
        }
      }

      form.addEventListener('submit', async (e) => {
        if (form.dataset.ajaxUserCreate === '1') return;
        const pwd = passwordInput?.value || '';
        const confirm = confirmInput?.value || '';
        const checkConfirm = Boolean(confirmInput);
        const errors = validatePasswordPair(pwd, confirm, { required, checkConfirm });

        if (form.dataset.ajaxPassword !== '1') {
          if (errors.length) {
            e.preventDefault();
            if (passwordInput) setFieldError(passwordInput, errors[0]);
            if (confirmInput && errors[0].toLowerCase().includes('match')) {
              setFieldError(confirmInput, errors[0]);
            }
            window.showErpToast?.('danger', errors[0]);
          }
          return;
        }

        e.preventDefault();
        if (errors.length) {
          if (passwordInput) setFieldError(passwordInput, errors[0]);
          if (confirmInput && errors[0].toLowerCase().includes('match')) {
            setFieldError(confirmInput, errors[0]);
          }
          window.showErpToast?.('danger', errors[0]);
          return;
        }

        const submitBtn = form.querySelector('[type="submit"]');
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
            const msg = data.error || (data.errors && Object.values(data.errors).flat()[0]) || 'Password update failed.';
            if (data.field === 'current_password') {
              const currentInput = form.querySelector('[name="current_password"]');
              setFieldError(currentInput, msg);
            }
            if (data.field === 'new_password' && passwordInput) setFieldError(passwordInput, msg);
            if (data.field === 'confirm_password' && confirmInput) setFieldError(confirmInput, msg);
            window.showErpToast?.('danger', msg);
            return;
          }
          window.showErpToast?.('success', data.message || 'Password updated successfully.');
          setTimeout(() => {
            window.location.href = data.redirect || '/';
          }, 700);
        } catch (_) {
          window.showErpToast?.('danger', 'Could not update password. Try again.');
        } finally {
          if (submitBtn) submitBtn.disabled = false;
        }
      });
    });
  }

  window.ErpPasswordValidation = {
    policyErrors,
    validatePasswordPair,
    ajaxValidatePassword,
    bindPasswordField,
    renderChecklist,
    setFieldError,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPasswordForms);
  } else {
    initPasswordForms();
  }
})();
