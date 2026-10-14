const pluggyForm = document.querySelector("#pluggy-xp-form");
const pluggyPreviewButton = document.querySelector("#pluggy-xp-preview-button");
const xpImportButton = document.querySelector("#pluggy-xp-import-button");
const pluggyRemoveButton = document.querySelector("#pluggy-xp-remove");
const interPreviewButton = document.querySelector("#pluggy-inter-preview-button");
const interImportButton = document.querySelector("#pluggy-inter-import-button");
let interPreviewToken = null;
let xpPreviewToken = null;

function interStatus(message, error = false) {
  const target = document.querySelector("#pluggy-inter-status");
  target.textContent = message;
  target.classList.toggle("error", error);
}

function clearInterPreview() {
  interPreviewToken = null;
  interImportButton.disabled = true;
  const target = document.querySelector("#pluggy-inter-preview");
  target.replaceChildren();
  target.hidden = true;
}

function pluggyStatus(message, error = false) {
  const target = document.querySelector("#pluggy-xp-status");
  target.textContent = message;
  target.classList.toggle("error", error);
}

async function refreshPluggyStatus() {
  try {
    const result = await request("/investments/pluggy/status");
    pluggyPreviewButton.disabled = !result.configured;
    interPreviewButton.disabled = !result.inter_configured;
    pluggyRemoveButton.hidden = !result.credentials_saved;
    for (const field of ["client_id", "client_secret"]) {
      pluggyForm.elements[field].required = !result.credentials_saved;
      pluggyForm.elements[field].placeholder = result.credentials_saved ? "Deixe vazio para manter" : "";
    }
    document.querySelector("#pluggy-xp-state").textContent = result.configured
      ? `XP configurada${result.inter_configured ? " · Inter configurado" : ""}`
      : result.inter_configured ? "Inter configurado · XP pendente" : "Conexão ainda não configurada";
    const synced = Object.entries(result.last_sync || {}).map(([source, value]) =>
      `${source}: ${new Date(value).toLocaleString("pt-BR")}`);
    if (synced.length) document.querySelector("#pluggy-xp-state").textContent += " · Última sincronização: " + synced.join(" · ");
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
  heading.textContent = `${data.snapshot.assets.length} ativos · ${data.count} movimentações da XP`;
  target.append(heading);
  for (const asset of data.snapshot.assets) {
    target.append(pluggyRow(asset.name,
      `${asset.category} · ${money.format(asset.value_cents / 100)}${asset.quote_date ? ` · cota de ${formatDate(asset.quote_date)}` : ""}`));
  }
  if (data.period) {
    target.append(pluggyRow("Período substituído", `${formatDate(data.period.starts_on)} a ${formatDate(data.period.ends_on)} · ${data.replaced_count} registros antigos`));
  }
  target.append(pluggyRow("Ativos + saldo disponível", `${money.format(data.snapshot.total_cents / 100)} · proventos a receber não informados`));
  const note = document.createElement("p");
  note.textContent = data.note;
  target.append(note);
  target.hidden = false;
}

function renderInterPreview(data) {
  const target = document.querySelector("#pluggy-inter-preview");
  target.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = `${data.new_count} lançamento(s) da Pluggy substituirão ${data.replaced_count} registros locais`;
  target.append(title);
  for (const account of data.accounts) {
    target.append(pluggyRow(account.name,
      `${account.type === "card" ? "Cartão" : "Conta"} · ${account.received} transações recebidas · destino: ${account.source}`));
  }
  for (const period of data.periods) {
    target.append(pluggyRow(period.source, `Substituir de ${formatDate(period.starts_on)} a ${formatDate(period.ends_on)}`));
  }
  if (data.bank_balance) {
    target.append(pluggyRow("Saldo da conta",
      `${money.format(data.bank_balance.amount_cents / 100)} em ${formatDate(data.bank_balance.as_of)}`));
  }
  const skipped = Object.entries(data.skipped);
  if (skipped.length) {
    const note = document.createElement("p");
    const labels = { historical: "anteriores ao início da conexão", pending: "pendentes", zero: "valor zero", card_payment: "pagamentos de fatura",
      no_bill: "sem mês da fatura", existing_statement: "cobertos por extratos/faturas existentes",
      manual_match: "já lançados manualmente", already_imported: "já importados da Pluggy" };
    note.textContent = "Ignorados: " + skipped.map(([reason, count]) => `${count} ${labels[reason] || reason}`).join(" · ");
    target.append(note);
  }
  for (const row of data.new_items) {
    target.append(pluggyRow(row.description,
      `${formatDate(row.occurred_on)} · ${row.kind === "expense" ? "Saída" : "Entrada"} ${money.format(row.amount_cents / 100)} · ${row.invoice_month || "conta"} · ${row.category}${row.excluded ? " (fora dos gastos)" : ""}${row.pending ? " · provisório" : ""}`));
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
      inter_item_id: pluggyForm.elements.inter_item_id.value,
    }) });
    pluggyForm.reset();
    clearInterPreview();
    xpPreviewToken = null;
    xpImportButton.disabled = true;
    pluggyStatus("Conexão salva. Confira XP e/ou Inter pelos botões abaixo.");
    await refreshPluggyStatus();
  } catch (error) { pluggyStatus(error.message, true); }
  finally { button.disabled = false; }
});

pluggyPreviewButton.addEventListener("click", async () => {
  pluggyPreviewButton.disabled = true;
  pluggyStatus("Consultando a Pluggy...");
  try {
    xpPreviewToken = null;
    xpImportButton.disabled = true;
    const data = await request("/investments/pluggy/sync-preview", { method: "POST", body: "{}" });
    renderPluggyPreview(data);
    xpPreviewToken = data.preview_token;
    xpImportButton.disabled = false;
    pluggyStatus("Confira as datas e os ativos. Ao sincronizar, a Pluggy substituirá o período mostrado.");
  } catch (error) { pluggyStatus(error.message, true); }
  finally { await refreshPluggyStatus(); }
});

xpImportButton.addEventListener("click", async () => {
  if (!xpPreviewToken) return;
  xpImportButton.disabled = true;
  try {
    const result = await request("/investments/pluggy/import", {
      method: "POST", body: JSON.stringify({ preview_token: xpPreviewToken }),
    });
    xpPreviewToken = null;
    pluggyStatus(`${result.imported} movimentações substituíram ${result.replaced} registros; ${result.asset_count} ativos atualizados. Posição guardada no histórico de rendimento. Cópia de recuperação salva localmente.`);
    document.dispatchEvent(new Event("investment-data-change"));
    await refresh();
  } catch (error) { pluggyStatus(error.message, true); }
});

pluggyRemoveButton.addEventListener("click", async () => {
  pluggyRemoveButton.disabled = true;
  try {
    await request("/investments/pluggy/config", { method: "DELETE" });
    document.querySelector("#pluggy-xp-preview").replaceChildren();
    document.querySelector("#pluggy-xp-preview").hidden = true;
    clearInterPreview();
    xpPreviewToken = null;
    xpImportButton.disabled = true;
    pluggyStatus("Credenciais removidas deste computador.");
    await refreshPluggyStatus();
  } catch (error) { pluggyStatus(error.message, true); }
  finally { pluggyRemoveButton.disabled = false; }
});

interPreviewButton.addEventListener("click", async () => {
  interPreviewButton.disabled = true;
  clearInterPreview();
  interStatus("Consultando conta e cartão do Inter na Pluggy...");
  try {
    const data = await request("/accounts/pluggy/inter/preview", { method: "POST", body: "{}" });
    renderInterPreview(data);
    interPreviewToken = data.preview_token;
    interImportButton.disabled = !(data.new_count || data.bank_balance);
    interStatus("Confira as datas e as compras novas. O histórico de faturas e extratos anterior ao início da conexão será preservado.");
  } catch (error) { interStatus(error.message, true); }
  finally { await refreshPluggyStatus(); }
});

interImportButton.addEventListener("click", async () => {
  if (!interPreviewToken) return;
  interImportButton.disabled = true;
  try {
    const result = await request("/accounts/pluggy/inter/import", {
      method: "POST", body: JSON.stringify({ preview_token: interPreviewToken }),
    });
    clearInterPreview();
    interStatus(`${result.imported} lançamentos substituíram ${result.replaced} registros; ${result.categories_preserved} categorias preservadas.${result.balance_updated ? " Saldo atualizado." : ""} Cópia de recuperação salva localmente.`);
    document.dispatchEvent(new Event("investment-data-change"));
    await refresh();
  } catch (error) {
    interStatus(error.message, true);
    clearInterPreview();
  }
});

document.addEventListener("settings-view-resume", refreshPluggyStatus);
refreshPluggyStatus();
