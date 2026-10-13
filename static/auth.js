const localAuth = (() => {
  const overlay = document.querySelector("#local-auth-overlay");
  const button = document.querySelector("#local-auth-button");
  const status = document.querySelector("#local-auth-status");
  const lockButton = document.querySelector("#local-lock-button");
  let token = null;
  let resolveGate;
  let gate = new Promise(resolve => { resolveGate = resolve; });
  let timer = null;
  let reopenOnVisible = false;

  function lock() {
    if (token) {
      token = null;
      gate = new Promise(resolve => { resolveGate = resolve; });
    }
    window.clearTimeout(timer);
    document.body.classList.add("locked");
    overlay.hidden = false;
    status.textContent = "";
  }

  async function unlock() {
    button.disabled = true;
    status.textContent = "Aguardando confirmação no Touch ID...";
    status.classList.remove("error");
    try {
      const response = await fetch("/api/v1/local-auth/unlock", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: "{}", cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok || !result.token) throw new Error(result.error || "Touch ID não aprovado.");
      if (document.hidden) {
        await fetch("/api/v1/local-auth/lock", {
          method: "POST", headers: { "Content-Type": "application/json", "X-Local-Session": result.token },
          body: "{}", cache: "no-store", keepalive: true,
        });
        return;
      }
      token = result.token;
      document.body.classList.remove("locked");
      overlay.hidden = true;
      resolveGate(token);
      timer = window.setTimeout(() => window.location.reload(), result.session_seconds * 1000);
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("error");
    } finally {
      button.disabled = false;
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

  button.addEventListener("click", unlock);
  lockButton.addEventListener("click", lockAndReload);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && token) {
      const oldToken = token;
      lock();
      reopenOnVisible = true;
      void fetch("/api/v1/local-auth/lock", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Local-Session": oldToken },
        body: "{}", cache: "no-store", keepalive: true,
      }).catch(() => {});
    } else if (!document.hidden && reopenOnVisible) {
      window.location.reload();
    }
  });
  return { ready: () => gate, token: () => token, lock };
})();
