const webhookForm = document.querySelector("#webhook-form");
let webhookEnabled = false;
let webhookBusy = false;

function webhookStatus(message, error = false) {
  const target = document.querySelector("#webhook-status");
  target.textContent = message;
  target.classList.toggle("error", error);
}

async function refreshWebhookStatus() {
  const data = await request("/pluggy/webhooks/status");
  webhookEnabled = data.enabled;
  webhookForm.elements.url.value = data.url;
  webhookForm.elements.enabled.checked = data.enabled;
  document.querySelector("#webhook-register").disabled = !data.configured;
  document.querySelector("#webhook-check").disabled = !data.configured || !data.enabled;
  document.querySelector("#webhook-disable").hidden = !data.configured && !data.keys_saved;
  document.querySelector("#webhook-state").textContent = data.enabled ? "Sincronização ativada" : data.configured ? "Receptor salvo · sincronização pausada" : "Aguardando receptor";
  const target = document.querySelector("#webhook-notices");
  target.replaceChildren();
  for (const item of data.notices) {
    target.append(pluggyRow(item.source, `${item.notice} · ${new Date(item.processed_at).toLocaleString("pt-BR")}`));
  }
}

async function checkWebhooks(manual = false) {
  if (webhookBusy || !localAuth.token() || document.hidden || !webhookEnabled) return;
  webhookBusy = true;
  try {
    const result = await request("/pluggy/webhooks/check", { method: "POST", body: "{}" });
    if (result.processed || manual || result.errors?.length) {
      const errors = (result.errors || []).map(e => `${e.source}: ${e.message}`).join(" · ");
      webhookStatus(result.synchronized.length ? `Atualizado pelos avisos: ${result.synchronized.join(" e ")}. ${errors}` : errors || result.message,
        Boolean(errors));
      await refreshWebhookStatus();
    }
    if (result.synchronized.length) {
      document.dispatchEvent(new Event("investment-data-change"));
      await refresh();
    }
  } catch (error) { webhookStatus(error.message, true); }
  finally { webhookBusy = false; }
}

document.querySelector("#webhook-setup").addEventListener("click", async () => {
  try {
    const data = await request("/pluggy/webhooks/setup", { method: "POST", body: "{}" });
    document.querySelector("#webhook-variables").value = Object.entries(data.variables).map(([key,value]) => `${key}=${value}`).join("\n");
    document.querySelector("#webhook-setup-output").hidden = false;
    webhookStatus(data.note);
    await refreshWebhookStatus();
  } catch (error) { webhookStatus(error.message, true); }
});

document.querySelector("#webhook-hide-keys").addEventListener("click", () => {
  document.querySelector("#webhook-variables").value = "";
  document.querySelector("#webhook-setup-output").hidden = true;
});

webhookForm.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await request("/pluggy/webhooks/config", { method:"POST", body:JSON.stringify({
      url:webhookForm.elements.url.value, read_token:webhookForm.elements.read_token.value,
      enabled:webhookForm.elements.enabled.checked,
    }) });
    webhookForm.elements.read_token.value = "";
    webhookStatus("Receptor salvo. Registre os avisos na Pluggy para começar.");
    await refreshWebhookStatus();
  } catch (error) { webhookStatus(error.message, true); }
});

document.querySelector("#webhook-register").addEventListener("click", async () => {
  try {
    const data = await request("/pluggy/webhooks/register", { method:"POST", body:"{}" });
    webhookStatus(`${data.registered} tipos de aviso registrados. A Pluggy avisará quando suas conexões atualizarem.`);
    await checkWebhooks();
  } catch (error) { webhookStatus(error.message, true); }
});
document.querySelector("#webhook-check").addEventListener("click", () => checkWebhooks(true));
document.querySelector("#webhook-disable").addEventListener("click", async () => {
  try {
    await request("/pluggy/webhooks/config", { method:"DELETE" });
    webhookStatus("Receptor desconectado deste Mac. Para parar os avisos na nuvem, remova os webhooks no Dashboard da Pluggy.");
    await refreshWebhookStatus();
  } catch (error) { webhookStatus(error.message, true); }
});

localAuth.ready().then(async () => {
  try { await refreshWebhookStatus(); await checkWebhooks(); }
  catch (error) { webhookStatus(error.message, true); }
});
window.setInterval(() => checkWebhooks(), 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) void checkWebhooks(); });
document.addEventListener("settings-view-resume", () => {
  refreshWebhookStatus().catch(error => webhookStatus(error.message, true));
});
