/* ==========================================================================
   STOCKVISION AI - CLIENT JAVASCRIPT
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const signinForm = document.getElementById('signinForm');
  const signupEntrepriseForm = document.getElementById('signupEntrepriseForm');
  const signupAdminForm = document.getElementById('signupAdminForm');

  const signinError = document.getElementById('signinError');
  const signinSuccess = document.getElementById('signinSuccess');
  const signupError = document.getElementById('signupError');
  const signupSuccess = document.getElementById('signupSuccess');

  function clearAlerts() {
    [signinError, signinSuccess, signupError, signupSuccess].forEach(el => {
      if (el) { el.hidden = true; el.textContent = ''; }
    });
  }

  // ===== 1. ROLE TOGGLE (Entreprise vs Administrateur) =====
  const roleButtons = document.querySelectorAll('.auth-role-btn');
  roleButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      roleButtons.forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');

      const role = btn.getAttribute('data-role');
      clearAlerts();

      if (role === 'administrateur') {
        if (signupEntrepriseForm) signupEntrepriseForm.hidden = true;
        if (signupAdminForm) signupAdminForm.hidden = false;
      } else {
        if (signupEntrepriseForm) signupEntrepriseForm.hidden = false;
        if (signupAdminForm) signupAdminForm.hidden = true;
      }
    });
  });

  // ===== 2. PASSWORD VISIBILITY TOGGLE =====
  document.querySelectorAll('.auth-toggle-pwd').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-target');
      const input = document.getElementById(targetId);
      if (!input) return;

      if (input.type === 'password') {
        input.type = 'text';
        btn.classList.add('active');
      } else {
        input.type = 'password';
        btn.classList.remove('active');
      }
    });
  });

  // ===== 3. FORM SUBMISSIONS & AUTH API =====

  function setLoading(form, isLoading) {
    const submitBtn = form.querySelector('button[type="submit"]');
    if (!submitBtn) return;
    const textSpan = submitBtn.querySelector('.auth-submit-text');
    const spinnerSpan = submitBtn.querySelector('.auth-spinner');

    submitBtn.disabled = isLoading;
    if (spinnerSpan) spinnerSpan.hidden = !isLoading;
    if (textSpan) textSpan.style.opacity = isLoading ? '0.7' : '1';
  }

  // --- Sign In Form Submit ---
  if (signinForm) {
    signinForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearAlerts();

      const email = document.getElementById('signinEmail')?.value.trim();
      const password = document.getElementById('signinPassword')?.value;

      if (!email || !password) {
        showError(signinError, 'Veuillez remplir tous les champs obligatoires.');
        return;
      }

      setLoading(signinForm, true);

      try {
        const response = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password })
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || 'Email ou mot de passe incorrect.');
        }

        if (data.access_token) {
          localStorage.setItem('stockvision_token', data.access_token);
        }

        if (signinSuccess) {
          signinSuccess.hidden = false;
          signinSuccess.className = 'auth-alert auth-alert-success';
          signinSuccess.textContent = 'Connexion réussie ! Redirection...';
        }

        setTimeout(() => {
          window.location.href = '/';
        }, 1000);

      } catch (err) {
        showError(signinError, err.message);
      } finally {
        setLoading(signinForm, false);
      }
    });
  }

  // --- Sign Up Entreprise Form Submit ---
  if (signupEntrepriseForm) {
    signupEntrepriseForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearAlerts();

      const nom = document.getElementById('signupNomEntreprise')?.value.trim();
      const secteur_activite = document.getElementById('signupSecteur')?.value.trim();
      const email = document.getElementById('signupEmailEnt')?.value.trim();
      const telephone = document.getElementById('signupTel')?.value.trim();
      const password = document.getElementById('signupPwdEnt')?.value;
      const password_confirm = document.getElementById('signupPwdEntConfirm')?.value;
      const terms = signupEntrepriseForm.querySelector('input[name="terms"]')?.checked;

      if (!nom || !secteur_activite || !email || !password || !password_confirm) {
        showError(signupError, 'Veuillez remplir tous les champs requis.');
        return;
      }

      if (password !== password_confirm) {
        showError(signupError, 'Les mots de passe ne correspondent pas.');
        return;
      }

      if (password.length < 8) {
        showError(signupError, 'Le mot de passe doit contenir au moins 8 caractères.');
        return;
      }

      if (!terms) {
        showError(signupError, 'Veuillez accepter les conditions d\'utilisation.');
        return;
      }

      setLoading(signupEntrepriseForm, true);

      try {
        const response = await fetch('/api/auth/register/entreprise', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            nom,
            secteur_activite,
            email,
            telephone: telephone || null,
            password
          })
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || 'Erreur lors de la création du compte entreprise.');
        }

        if (signupSuccess) {
          signupSuccess.hidden = false;
          signupSuccess.textContent = data.message || 'Compte entreprise créé avec succès ! Vous pouvez maintenant vous connecter.';
        }

        signupEntrepriseForm.reset();

        setTimeout(() => {
          window.location.href = '/connexion';
        }, 1800);

      } catch (err) {
        showError(signupError, err.message);
      } finally {
        setLoading(signupEntrepriseForm, false);
      }
    });
  }

  // --- Sign Up Admin Form Submit ---
  if (signupAdminForm) {
    signupAdminForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearAlerts();

      const nom = document.getElementById('signupNomAdmin')?.value.trim();
      const email = document.getElementById('signupEmailAdmin')?.value.trim();
      const admin_code = document.getElementById('signupCodeAdmin')?.value.trim();
      const password = document.getElementById('signupPwdAdmin')?.value;
      const password_confirm = document.getElementById('signupPwdAdminConfirm')?.value;

      if (!nom || !email || !admin_code || !password || !password_confirm) {
        showError(signupError, 'Veuillez remplir tous les champs de l\'administration.');
        return;
      }

      if (password !== password_confirm) {
        showError(signupError, 'Les mots de passe ne correspondent pas.');
        return;
      }

      if (password.length < 8) {
        showError(signupError, 'Le mot de passe doit contenir au moins 8 caractères.');
        return;
      }

      setLoading(signupAdminForm, true);

      try {
        const response = await fetch('/api/auth/register/admin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            nom,
            email,
            admin_code,
            password
          })
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || 'Code administrateur invalide ou erreur d\'inscription.');
        }

        if (signupSuccess) {
          signupSuccess.hidden = false;
          signupSuccess.textContent = data.message || 'Compte administrateur créé avec succès ! Connectez-vous.';
        }

        signupAdminForm.reset();

        setTimeout(() => {
          window.location.href = '/connexion';
        }, 1800);

      } catch (err) {
        showError(signupError, err.message);
      } finally {
        setLoading(signupAdminForm, false);
      }
    });
  }

  function showError(el, message) {
    if (!el) return;
    el.hidden = false;
    el.className = 'auth-alert auth-alert-error';
    el.textContent = message;
  }

  // Active Navbar Link Highlight
  const currentPath = window.location.pathname;
  document.querySelectorAll('.navbar-nav .nav-link').forEach(link => {
    const href = link.getAttribute('href');
    if (href === currentPath || (href !== '/' && currentPath.startsWith(href))) {
      link.classList.add('active');
    }
  });
});
