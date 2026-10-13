const pluggyForm = document.querySelector("#pluggy-xp-form");
const pluggyPreviewButton = document.querySelector("#pluggy-xp-preview-button");
const pluggyRemoveButton = document.querySelector("#pluggy-xp-remove");
const interPreviewButton = document.querySelector("#pluggy-inter-preview-button");
const interImportButton = document.querySelector("#pluggy-inter-import-button");
let interPreviewToken = null;

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

function renderInterPreview(data) {
  const target = document.querySelector("#pluggy-inter-preview");
  target.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = `${data.new_count} lançamento(s) novo(s) para importar`;
  target.append(title);
  for (const account of data.accounts) {
    target.append(pluggyRow(account.name,
      `${account.type === "card" ? "Cartão" : "Conta"} · ${account.received} transações recebidas · destino: ${account.source}`));
  }
  if (data.bank_balance) {
    target.append(pluggyRow("Saldo da conta",
      `${money.format(data.bank_balance.amount_cents / 100)} em ${formatDate(data.bank_balance.as_of)}`));
  }
  const skipped = Object.entries(data.skipped);
  if (skipped.length) {
    const note = document.createElement("p");
    const labels = { pending: "pendentes", zero: "valor zero", card_payment: "pagamentos de fatura",
      no_bill: "sem mês da fatura", existing_statement: "cobertos por extratos/faturas existentes",
      manual_match: "já lançados manualmente", already_imported: "já importados da Pluggy" };
    note.textContent = "Ignorados: " + skipped.map(([reason, count]) => `${count} ${labels[reason] || reason}`).join(" · ");
    target.append(note);
  }
  for (const row of data.new_items) {
    target.append(pluggyRow(row.description,
      `${formatDate(row.occurred_on)} · ${row.kind === "expense" ? "Saída" : "Entrada"} ${money.format(row.amount_cents / 100)} · ${row.invoice_month || "conta"} · ${row.category}${row.excluded ? " (fora dos gastos)" : ""}`));
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
    pluggyStatus("Conexão salva. Confira XP e/ou Inter pelos botões abaixo.");
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
    clearInterPreview();
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
    interStatus("Prévia pronta. Confira as contas, os lançamentos novos e os ignorados antes de importar.");
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
    interStatus(`${result.imported} lançamento(s) importado(s), ${result.skipped} já presentes.${result.balance_updated ? " Saldo atualizado." : ""}`);
    await refresh();
  } catch (error) {
    interStatus(error.message, true);
    clearInterPreview();
  }
});

document.addEventListener("settings-view-resume", refreshPluggyStatus);
refreshPluggyStatus();
