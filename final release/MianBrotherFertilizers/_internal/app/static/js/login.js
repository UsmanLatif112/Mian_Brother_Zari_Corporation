(function () {
  const form = document.getElementById('login-form');
  if (!form) return;

  const username = document.getElementById('username');
  const password = document.getElementById('password');
  const toggle = document.getElementById('toggle-password');
  const submitBtn = document.getElementById('login-submit');

  toggle?.addEventListener('click', () => {
    const hidden = password.type === 'password';
    password.type = hidden ? 'text' : 'password';
    toggle.querySelector('i').className = hidden ? 'fa-regular fa-eye-slash' : 'fa-regular fa-eye';
  });

  form.addEventListener('submit', (e) => {
    form.classList.add('was-validated');
    const userOk = username.value.trim().length > 0;
    const passOk = password.value.length > 0;
    if (!userOk || !passOk) {
      e.preventDefault();
      showAuthError('Please enter your username and password.');
      if (!userOk) username.classList.add('is-invalid');
      if (!passOk) password.classList.add('is-invalid');
      return;
    }
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Signing in…';
  });

  [username, password].forEach((el) => {
    el?.addEventListener('input', () => {
      el.classList.remove('is-invalid');
    });
  });

  function showAuthError(message) {
    const box = document.getElementById('auth-alert');
    const text = document.getElementById('auth-alert-text');
    if (!box || !text) return;
    box.classList.remove('d-none', 'auth-alert--info');
    box.classList.add('auth-alert--error');
    box.querySelector('.auth-alert__icon i').className = 'fa-solid fa-circle-xmark';
    box.querySelector('strong').textContent = 'Sign in failed';
    text.textContent = message;
  }

  if (document.querySelector('.auth-alert--error')) {
    password?.focus();
  }
})();
