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
    expiresAt = 0;
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
      token = result.token;
      method = accessMethod;
      document.body.classList.remove("locked");
      overlay.hidden = true;
      resolveGate(token);
      expiresAt = Date.now() + result.session_seconds * 1000;
      timer = window.setTimeout(() => {
        lock();
        window.location.reload();
      }, result.session_seconds * 1000);
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
      if (response.status === 401) { lock(); window.location.reload(); return; }
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

  async function lockAndReload() {
    if (token) {
      try {
        await fetch("/api/v1/local-auth/lock", {
          method: "POST", headers: { "Content-Type": "application/json", "X-Local-Session": token },
          body: "{}", cache: "no-store", keepalive: true,
        });
      } catch (_) { /* The session also expires on the server. */ }
    }
    lock();
    window.location.reload();
  }

  button.addEventListener("click", () => unlock("touch_id"));
  passwordLogin.addEventListener("submit", event => { event.preventDefault(); unlock("password"); });
  settingsForm.addEventListener("submit", event => { event.preventDefault(); configurePassword(false); });
  removeButton.addEventListener("click", () => configurePassword(true));
  lockButton.addEventListener("click", lockAndReload);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && token && Date.now() >= expiresAt) {
      lock();
      window.location.reload();
    }
  });
  refreshStatus();
  return { ready: () => gate, token: () => token, lock };
})();
