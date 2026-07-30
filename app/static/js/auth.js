/* ==========================================================================
   STOCKVISION AI — AUTH (signin/signup modal + standalone pages + OAuth)
   Single source of truth: this script runs both inside the popup (home,
   features, services, about, contact — anywhere the modal partial is
   included) and on the dedicated /connexion and /inscription pages (where
   the modal is intentionally omitted to avoid duplicate-ID conflicts).
   ========================================================================== */
document.addEventListener('DOMContentLoaded', () => {
  const overlay = document.getElementById('authOverlay');
  const signinPanel = document.getElementById('signinPanel');
  const signupPanel = document.getElementById('signupPanel');
  const closeBtn = document.getElementById('authCloseBtn');
  const signinForm = document.getElementById('signinForm');
  const signupEntForm = document.getElementById('signupEntrepriseForm');
  const signinError = document.getElementById('signinError');
  const signinSuccess = document.getElementById('signinSuccess');
  const signupError = document.getElementById('signupError');
  const signupSuccess = document.getElementById('signupSuccess');

  function showAlert(el, message, isSuccess = false) {
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    el.classList.toggle('auth-alert-success', isSuccess);
    el.classList.toggle('auth-alert-error', !isSuccess);
  }

  function hideAlerts() {
    [signinError, signinSuccess, signupError, signupSuccess].forEach(el => {
      if (el) el.hidden = true;
    });
  }

  function setLoading(form, loading) {
    const btn = form.querySelector('.auth-submit');
    const text = btn?.querySelector('.auth-submit-text');
    const spinner = btn?.querySelector('.auth-spinner');
    if (!btn) return;
    btn.disabled = loading;
    if (text) text.hidden = loading;
    if (spinner) spinner.hidden = !loading;
  }

  function formToObject(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  // ===== Modal-only behaviour (only present on pages that include the popup) =====
  if (overlay) {
    const focusFirstField = panel => {
      const field = panel.querySelector('input:not([type=hidden])');
      if (field) window.requestAnimationFrame(() => field.focus({ preventScroll: true }));
    };

    const switchPanel = panel => {
      hideAlerts();
      const isSignin = panel === 'signin';
      signinPanel.hidden = !isSignin;
      signupPanel.hidden = isSignin;
      document.getElementById('authModalTitle').textContent = isSignin ? 'Connexion' : 'Inscription';
      focusFirstField(isSignin ? signinPanel : signupPanel);
    };

    const openAuth = (panel = 'signin') => {
      switchPanel(panel);
      overlay.classList.add('is-open');
      overlay.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
    };

    const closeAuth = () => {
      overlay.classList.remove('is-open');
      overlay.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
      hideAlerts();
    };

    document.querySelectorAll('[data-auth-open]').forEach(trigger => {
      trigger.addEventListener('click', e => {
        e.preventDefault();
        openAuth(trigger.dataset.authOpen);
      });
    });

    document.querySelectorAll('[data-auth-switch]').forEach(btn => {
      btn.addEventListener('click', () => switchPanel(btn.dataset.authSwitch));
    });

    closeBtn?.addEventListener('click', closeAuth);
    overlay.addEventListener('click', e => {
      if (e.target === overlay) closeAuth();
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && overlay.classList.contains('is-open')) closeAuth();
    });

    // Deep link support: /?auth=signin or /?auth=signup opens the popup
    // straight away from anywhere on the site.
    const params = new URLSearchParams(window.location.search);
    if (params.get('auth') === 'signin') {
      openAuth('signin');
      history.replaceState({}, '', window.location.pathname);
    } else if (params.get('auth') === 'signup') {
      openAuth('signup');
      history.replaceState({}, '', window.location.pathname);
    }

    window.openAuthModal = openAuth;
  }

  // ===== Shared behaviour: identical markup/IDs on the modal and the
  // standalone pages, so this logic works for both without duplication =====

  document.querySelectorAll('.auth-toggle-pwd').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = document.getElementById(btn.dataset.target);
      if (!input) return;
      input.type = input.type === 'password' ? 'text' : 'password';
    });
  });

  // OAuth — redirect to the backend authorization endpoint, which handles
  // the provider handshake and comes back with a session token.
  document.querySelectorAll('[data-oauth]').forEach(btn => {
    btn.addEventListener('click', () => {
      window.location.href = `/api/auth/oauth/${btn.dataset.oauth}/login`;
    });
  });

  signinForm?.addEventListener('submit', async e => {
    e.preventDefault();
    hideAlerts();
    const data = formToObject(signinForm);
    setLoading(signinForm, true);

    try {
      // /connexion sets an httponly session cookie server-side and tells us
      // where to go next — no token ever touches JS/localStorage.
      const res = await fetch('/connexion', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: data.email, password: data.password }),
      });
      const json = await res.json();

      if (!res.ok) {
        showAlert(signinError, json.detail || 'Erreur de connexion.');
        return;
      }

      showAlert(signinSuccess, 'Connexion réussie ! Redirection...', true);
      setTimeout(() => {
        window.location.href = json.redirect || '/';
      }, 500);
    } catch {
      showAlert(signinError, 'Impossible de contacter le serveur.');
    } finally {
      setLoading(signinForm, false);
    }
  });

  signupEntForm?.addEventListener('submit', async e => {
    e.preventDefault();
    hideAlerts();
    const data = formToObject(signupEntForm);

    setLoading(signupEntForm, true);
    try {
      const res = await fetch('/inscription', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const json = await res.json();

      if (!res.ok) {
        showAlert(signupError, json.detail || "Erreur lors de l'inscription.");
        return;
      }

      signupEntForm.reset();
      showAlert(signupSuccess, `${json.message} ${json.detail || ''}`, true);
    } catch {
      showAlert(signupError, 'Impossible de contacter le serveur.');
    } finally {
      setLoading(signupEntForm, false);
    }
  });

  // OAuth error bridge — the backend redirects here as /?oauth_error=...
  // when Google/GitHub auth fails (success goes straight to the dashboard).
  const oauthParams = new URLSearchParams(window.location.search);
  if (oauthParams.get('oauth_error')) {
    showAlert(signinError || signupError, decodeURIComponent(oauthParams.get('oauth_error')));
    history.replaceState({}, '', window.location.pathname);
  }

  // Active navbar link highlight
  const currentPath = window.location.pathname;
  document.querySelectorAll('.navbar-nav .nav-link').forEach(link => {
    const href = link.getAttribute('href');
    if (href === currentPath || (href !== '/' && currentPath.startsWith(href))) {
      link.classList.add('active');
    }
  });
});
