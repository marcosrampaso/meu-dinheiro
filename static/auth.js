const localAuth = (() => {
  const overlay = document.querySelector("#local-auth-overlay");
  const button = document.querySelector("#local-auth-button");
  const status = document.querySelector("#local-auth-status");
  const lockButton = document.querySelector("#local-lock-button");
  const passwordLogin = document.querySelector("#local-password-login");
  const passwordButton = document.querySelector("#local-password-login-button");
  const settingsForm = document.querySelector("#local-password-settings");
  const settingsStatus = document.querySelector("#local-password-settings-status");
  const saveButton = document.querySelector("#local-password-save");
  const removeButton = document.querySelector("#local-password-remove");
  let token = null;
  let method = null;
  let resolveGate;
  let gate = new Promise(resolve => { resolveGate = resolve; });
  let timer = null;
  let expiresAt = 0;
  const storageKey = "local-idle-session-v1";
  let sessionMilliseconds = 300000;
  let lastActivityAt = 0;
  let lastReportedActivityAt = 0;
  let lastRenewedAt = 0;
  let activityTimer = null;
  let renewing = false;

  function rememberSession() {
    try {
      if (token) window.sessionStorage.setItem(storageKey, JSON.stringify({ token,
        last_activity_at: lastActivityAt, expires_at: expiresAt, session_seconds: sessionMilliseconds / 1000 }));
      else window.sessionStorage.removeItem(storageKey);
    } catch (_) { /* The current page still works when tab storage is unavailable. */ }
  }

  function checkIdle() {
    if (!token) return;
    window.clearTimeout(timer);
    if (Date.now() >= expiresAt) {
      lock();
      status.textContent = "Sessão pausada após cinco minutos sem atividade. Desbloqueie para continuar de onde parou.";
    } else timer = window.setTimeout(checkIdle, Math.max(1, expiresAt - Date.now()));
  }

  function establishSession(authToken, accessMethod, duration, lastActive, deadline) {
    token = authToken; method = accessMethod;
    sessionMilliseconds = duration * 1000;
    lastActivityAt = lastActive;
    lastReportedActivityAt = lastActive;
    lastRenewedAt = Date.now();
    expiresAt = Math.min(deadline, lastActive + sessionMilliseconds);
    rememberSession();
    document.body.classList.remove("locked"); overlay.hidden = true;
    resolveGate(token); checkIdle();
  }

  async function restoreSession() {
    let saved;
    try { saved = JSON.parse(window.sessionStorage.getItem(storageKey)); } catch (_) { return; }
    if (!saved) return;
    const now = Date.now();
    if (typeof saved.token !== "string" || saved.token.length > 100 ||
        !Number.isFinite(saved.last_activity_at) || saved.last_activity_at > now ||
        !Number.isFinite(saved.expires_at) || saved.expires_at <= now || saved.session_seconds !== 300) {
      rememberSession(); return;
    }
    try {
      const started = Date.now();
      const response = await fetch("/api/v1/local-auth/session", {
        headers: { "X-Local-Session": saved.token }, cache: "no-store",
      });
      const result = await response.json();
      const deadline = Math.min(saved.expires_at, started + result.remaining_seconds * 1000);
      if (!response.ok || !Number.isFinite(result.remaining_seconds) || result.remaining_seconds <= 0 ||
          result.session_seconds !== 300 || deadline <= Date.now()) { rememberSession(); return; }
      establishSession(saved.token, result.method, result.session_seconds, saved.last_activity_at, deadline);
      await refreshStatus();
    } catch (_) { rememberSession(); }
  }

  function queueActivity() {
    if (!token || renewing || activityTimer !== null || lastActivityAt <= lastReportedActivityAt) return;
    activityTimer = window.setTimeout(() => { activityTimer = null; void renewActivity(); },
      Math.max(0, 15000 - (Date.now() - lastRenewedAt)));
  }

  async function renewActivity() {
    if (!token || renewing || lastActivityAt <= lastReportedActivityAt) return;
    checkIdle(); if (!token) return;
    const authToken = token, activityAt = lastActivityAt;
    renewing = true; lastRenewedAt = Date.now();
    try {
      const response = await fetch("/api/v1/local-auth/activity", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Local-Session": authToken },
        body: JSON.stringify({ idle_for_ms: Math.max(0, Math.round(Date.now() - activityAt)) }),
        cache: "no-store", keepalive: true,
      });
      if (token !== authToken) return;
      if (response.status === 401) { lock(); status.textContent = "Sessão encerrada. Desbloqueie para continuar."; return; }
      if (response.ok) lastReportedActivityAt = Math.max(lastReportedActivityAt, activityAt);
    } catch (_) { /* Server expiry still applies; a later real interaction may retry. */ }
    finally {
      if (token === authToken) {
        renewing = false;
        if (lastActivityAt > activityAt) queueActivity();
      }
    }
  }

  function onActivity(event) {
    if (!event.isTrusted || document.hidden || !document.hasFocus() || !token) return;
    checkIdle(); if (!token) return;
    if (event.type === "pointermove" && Date.now() - lastActivityAt < 1000) return;
    lastActivityAt = Date.now(); expiresAt = lastActivityAt + sessionMilliseconds;
    rememberSession(); checkIdle(); queueActivity();
  }

  function renderStatus(result) {
    button.hidden = !result.touch_id_available;
    passwordLogin.hidden = !result.password_configured;
    removeButton.hidden = !result.password_configured;
    document.querySelector("#local-password-state").textContent = result.password_configured ? "Senha cadastrada" : "Somente Touch ID";
    settingsForm.elements.current_password.closest("label").hidden = method !== "password";
    const description = document.querySelector("#local-auth-description");
    description.textContent = result.password_configured
      ? "Desbloqueie com sua senha local ou com Touch ID, quando disponível."
      : result.touch_id_available ? "Use o Touch ID deste Mac. Depois, cadastre uma senha local em Configurações."
        : "Nenhuma forma de acesso está disponível. Ative o Touch ID deste Mac para cadastrar a primeira senha local.";
  }

  async function refreshStatus() {
    try {
      const response = await fetch("/api/v1/local-auth/status", { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Não foi possível verificar o acesso.");
      renderStatus(result);
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("error");
    }
  }

  function lock() {
    if (token) {
      token = null;
      gate = new Promise(resolve => { resolveGate = resolve; });
    }
    method = null;
    passwordLogin.reset();
    settingsForm.reset();
    window.clearTimeout(timer);
    window.clearTimeout(activityTimer); activityTimer = null; renewing = false;
    expiresAt = 0;
    lastActivityAt = 0; lastReportedActivityAt = 0;
    rememberSession();
    document.body.classList.add("locked");
    overlay.hidden = false;
    status.textContent = "";
  }

  async function unlock(accessMethod = "touch_id") {
    button.disabled = true;
    passwordButton.disabled = true;
    status.textContent = accessMethod === "password" ? "Verificando senha local..." : "Aguardando confirmação no Touch ID...";
    status.classList.remove("error");
    try {
      const response = await fetch(accessMethod === "password" ? "/api/v1/local-auth/password-login" : "/api/v1/local-auth/unlock", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: accessMethod === "password" ? JSON.stringify({ password: passwordLogin.elements.password.value }) : "{}",
        cache: "no-store",
      });
      const result = await response.json();
      passwordLogin.reset();
      if (!response.ok || !result.token) throw new Error(result.error || "Acesso não aprovado.");
      if (document.hidden) {
        await fetch("/api/v1/local-auth/lock", {
          method: "POST", headers: { "Content-Type": "application/json", "X-Local-Session": result.token },
          body: "{}", cache: "no-store", keepalive: true,
        });
        return;
      }
      const now = Date.now();
      establishSession(result.token, accessMethod, result.session_seconds, now, now + result.session_seconds * 1000);
      await refreshStatus();
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("error");
    } finally {
      button.disabled = false;
      passwordButton.disabled = false;
    }
  }

  async function configurePassword(remove = false) {
    if (!token) return;
    const password = settingsForm.elements.password.value;
    if (!remove && (password.length < 12 || password.length > 256)) {
      settingsStatus.textContent = "Use uma senha de 12 a 256 caracteres.";
      settingsStatus.classList.add("error");
      return;
    }
    if (!remove && password !== settingsForm.elements.password_confirmation.value) {
      settingsStatus.textContent = "As duas novas senhas precisam ser iguais.";
      settingsStatus.classList.add("error");
      return;
    }
    if (remove && !window.confirm("Remover a senha local? O acesso continuará somente por Touch ID.")) return;
    saveButton.disabled = true;
    removeButton.disabled = true;
    settingsStatus.classList.remove("error");
    settingsStatus.textContent = "Atualizando acesso local...";
    try {
      const response = await fetch("/api/v1/local-auth/password", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Local-Session": token },
        body: JSON.stringify({ password, current_password: settingsForm.elements.current_password.value, remove }),
        cache: "no-store",
      });
      const result = await response.json();
      settingsForm.reset();
      if (response.status === 401) { lock(); return; }
      if (!response.ok) throw new Error(result.error || "Não foi possível atualizar a senha local.");
      renderStatus(result);
      settingsStatus.textContent = remove ? "Senha removida. O acesso continua por Touch ID." : "Senha local salva. Ela estará disponível na próxima tela de acesso.";
    } catch (error) {
      settingsStatus.textContent = error.message;
      settingsStatus.classList.add("error");
    } finally {
      saveButton.disabled = false;
      removeButton.disabled = false;
    }
  }

  async function lockExplicitly() {
    if (token) {
      try {
        await fetch("/api/v1/local-auth/lock", {
          method: "POST", headers: { "Content-Type": "application/json", "X-Local-Session": token },
          body: "{}", cache: "no-store", keepalive: true,
        });
      } catch (_) { /* The session also expires on the server. */ }
    }
    lock();
  }

  button.addEventListener("click", () => unlock("touch_id"));
  passwordLogin.addEventListener("submit", event => { event.preventDefault(); unlock("password"); });
  settingsForm.addEventListener("submit", event => { event.preventDefault(); configurePassword(false); });
  removeButton.addEventListener("click", () => configurePassword(true));
  lockButton.addEventListener("click", lockExplicitly);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkIdle();
    else void renewActivity(); // Flush a preceding real interaction; hiding is not activity.
  });
  window.addEventListener("focus", checkIdle);
  window.addEventListener("pageshow", checkIdle);
  window.addEventListener("pagehide", () => { void renewActivity(); });
  for (const type of ["pointerdown", "pointermove", "keydown", "input", "wheel", "touchstart"]) {
    document.addEventListener(type, onActivity, { capture: true, passive: true });
  }
  button.disabled = true; passwordButton.disabled = true;
  void (async () => {
    await refreshStatus(); await restoreSession();
    button.disabled = false; passwordButton.disabled = false;
  })();
  return { ready: () => { checkIdle(); return gate; }, token: () => token, lock };
})();
