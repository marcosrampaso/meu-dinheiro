const pluggyForm = document.querySelector("#pluggy-xp-form");
const pluggyPreviewButton = document.querySelector("#pluggy-xp-preview-button");
const pluggyRemoveButton = document.querySelector("#pluggy-xp-remove");

function pluggyStatus(message, error = false) {
  const target = document.querySelector("#pluggy-xp-status");
  target.textContent = message;
  target.classList.toggle("error", error);
}

async function refreshPluggyStatus() {
  try {
    const result = await request("/investments/pluggy/status");
    pluggyPreviewButton.disabled = !result.configured;
    pluggyRemoveButton.hidden = !result.configured;
    document.querySelector("#pluggy-xp-state").textContent = result.configured
      ? "Credenciais salvas neste computador" : "Conexão ainda não configurada";
  } catch (error) { pluggyStatus(error.message, true); }
}

function pluggyRow(name, details) {
  const row = document.createElement("div");
  row.className = "manual-investment-row";
  const strong = document.createElement("strong");
  strong.textContent = name;
  const span = document.createElement("span");
  span.textContent = details;
  row.append(strong, span);
  return row;
}

function renderPluggyPreview(data) {
  const target = document.querySelector("#pluggy-xp-preview");
  target.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = `Pluggy: ${data.assets.length} ativo(s) · bruto ${money.format(data.gross_cents / 100)}`;
  target.append(heading);
  for (const asset of data.assets) {
    target.append(pluggyRow(asset.name,
      `${asset.type || "Ativo"} · bruto ${money.format(asset.gross_cents / 100)} · líquido ${money.format(asset.net_cents / 100)}`));
  }
  const previous = document.createElement("h3");
  previous.textContent = `Última planilha XP: ${data.existing_assets.length} ativo(s)`;
  target.append(previous);
  for (const asset of data.existing_assets) {
    target.append(pluggyRow(asset.name, money.format(asset.value_cents / 100)));
  }
  const note = document.createElement("p");
  note.textContent = data.note;
  target.append(note);
  target.hidden = false;
}

pluggyForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = pluggyForm.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await request("/investments/pluggy/config", { method: "POST", body: JSON.stringify({
      client_id: pluggyForm.elements.client_id.value,
      client_secret: pluggyForm.elements.client_secret.value,
      item_id: pluggyForm.elements.item_id.value,
    }) });
    pluggyForm.reset();
    pluggyStatus("Conexão salva. Clique em Conferir ativos recebidos.");
    await refreshPluggyStatus();
  } catch (error) { pluggyStatus(error.message, true); }
  finally { button.disabled = false; }
});

pluggyPreviewButton.addEventListener("click", async () => {
  pluggyPreviewButton.disabled = true;
  pluggyStatus("Consultando a Pluggy...");
  try {
    const data = await request("/investments/pluggy/preview", { method: "POST", body: "{}" });
    renderPluggyPreview(data);
    pluggyStatus("Prévia recebida. Compare os ativos com a última posição XP.");
  } catch (error) { pluggyStatus(error.message, true); }
  finally { await refreshPluggyStatus(); }
});

pluggyRemoveButton.addEventListener("click", async () => {
  pluggyRemoveButton.disabled = true;
  try {
    await request("/investments/pluggy/config", { method: "DELETE" });
    document.querySelector("#pluggy-xp-preview").replaceChildren();
    document.querySelector("#pluggy-xp-preview").hidden = true;
    pluggyStatus("Credenciais removidas deste computador.");
    await refreshPluggyStatus();
  } catch (error) { pluggyStatus(error.message, true); }
  finally { pluggyRemoveButton.disabled = false; }
});

document.addEventListener("investments-view-resume", refreshPluggyStatus);
refreshPluggyStatus();
