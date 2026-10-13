const importForm = document.querySelector("#import-form");
const importFile = importForm.elements.file;
const importStatus = document.querySelector("#import-status");
const selectedFilesView = document.querySelector("#selected-files");
const mappingPanel = document.querySelector("#mapping-panel");
const mappingFields = document.querySelector("#mapping-fields");
const importPreview = document.querySelector("#import-preview");
const importButton = document.querySelector("#import-button");
let selectedFiles = [];
let readyPayloads = [];
let inspectGeneration = 0;

function invalidateImportPreview() {
  readyPayloads = [];
  importButton.disabled = true;
  importPreview.hidden = true;
  importPreview.replaceChildren();
}

function setImportStatus(message, isError = false) {
  importStatus.textContent = message;
  importStatus.classList.toggle("error", isError);
}

function toBase64(bytes) {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 8192) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
  }
  return btoa(binary);
}

async function readStatement(file) {
  const isPdf = file.name.toLowerCase().endsWith(".pdf");
  const maximum = isPdf ? 5_000_000 : 2_000_000;
  if (file.size > maximum) throw new Error(`${file.name}: limite de ${maximum / 1_000_000} MB.`);
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (isPdf) return { filename: file.name, content_base64: toBase64(bytes) };
  let content;
  try {
    content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    content = new TextDecoder("windows-1252").decode(bytes);
  }
  return { filename: file.name, content };
}

function renderMapping(columns, suggestions) {
  mappingFields.replaceChildren();
  const labels = {
    date: "Data", description: "Descrição", amount: "Valor único",
    debit: "Débito", credit: "Crédito", id: "ID da transação (opcional)",
  };
  for (const [key, labelText] of Object.entries(labels)) {
    const label = document.createElement("label");
    label.textContent = labelText;
    const select = document.createElement("select");
    select.dataset.map = key;
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "— Nenhuma —";
    select.append(blank);
    columns.forEach((column, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = column;
      select.append(option);
    });
    if (suggestions[key] !== null && suggestions[key] !== undefined) {
      select.value = String(suggestions[key]);
    }
    label.append(select);
    mappingFields.append(label);
  }
}

function renderSelectedFiles() {
  selectedFilesView.replaceChildren();
  for (const entry of selectedFiles) {
    const row = document.createElement("div");
    row.className = "selected-file";
    const name = document.createElement("span");
    name.textContent = `${entry.data.filename} · ${entry.inspected.count} lançamentos encontrados`;
    row.append(name);
    if (entry.inspected.account_balance) {
      const balance = entry.inspected.account_balance;
      const snapshot = document.createElement("span");
      snapshot.textContent = `Saldo do extrato: ${money.format(balance.amount_cents / 100)} em ${formatDate(balance.as_of)}`;
      row.append(snapshot);
    }
    if (entry.inspected.reconciliation) {
      const check = document.createElement("span");
      check.textContent = entry.inspected.reconciliation.matches
        ? `Total conferido: ${money.format(entry.inspected.reconciliation.invoice_cents / 100)}`
        : `Atenção: total encontrado ${money.format(entry.inspected.reconciliation.parsed_invoice_cents / 100)}; fatura ${money.format(entry.inspected.reconciliation.invoice_cents / 100)}`;
      if (!entry.inspected.reconciliation.matches) check.className = "error";
      row.append(check);
    }
    if (entry.inspected.format === "pdf") {
      const label = document.createElement("label");
      label.textContent = "Mês da fatura";
      const month = document.createElement("input");
      month.type = "month";
      month.required = true;
      month.value = entry.inspected.statement_month || "";
      month.addEventListener("change", invalidateImportPreview);
      label.append(month);
      row.append(label);
      entry.monthInput = month;
    }
    selectedFilesView.append(row);
  }
}

async function inspectSelectedFiles() {
  const generation = ++inspectGeneration;
  invalidateImportPreview();
  selectedFiles = [];
  selectedFilesView.replaceChildren();
  mappingPanel.hidden = true;
  mappingFields.replaceChildren();
  const files = [...importFile.files];
  if (!files.length) return;
  if (files.length > 60 || files.reduce((total, file) => total + file.size, 0) > 100_000_000) {
    setImportStatus("Selecione até 60 arquivos e 100 MB por lote.", true);
    return;
  }
  if (files.length > 1 && files.some(file => !file.name.toLowerCase().endsWith(".pdf"))) {
    setImportStatus("Selecione várias faturas PDF ou apenas um CSV/OFX por vez.", true);
    return;
  }
  setImportStatus(`Lendo ${files.length} ${files.length === 1 ? "arquivo" : "arquivos"}...`);
  try {
    for (const [index, file] of files.entries()) {
      setImportStatus(`Lendo arquivo ${index + 1} de ${files.length}: ${file.name}`);
      const data = await readStatement(file);
      const inspected = await request("/imports/inspect", {
        method: "POST",
        body: JSON.stringify({ ...data, pdf_password: importForm.elements.pdf_password.value }),
      });
      if (generation !== inspectGeneration) return;
      selectedFiles.push({ data, inspected, monthInput: null });
    }
    if (selectedFiles[0].inspected.format === "csv") {
      renderMapping(selectedFiles[0].inspected.columns, selectedFiles[0].inspected.suggested_mapping);
      mappingPanel.hidden = false;
    } else if (selectedFiles[0].inspected.format === "pdf") {
      const suggestions = new Set(selectedFiles.map(entry => entry.inspected.suggested_expense_sign));
      if (suggestions.size === 1) importForm.elements.expense_sign.value = [...suggestions][0];
    }
    renderSelectedFiles();
    const total = selectedFiles.reduce((sum, entry) => sum + entry.inspected.count, 0);
    setImportStatus(`${selectedFiles.length} ${selectedFiles.length === 1 ? "arquivo lido" : "arquivos lidos"}; ${total} lançamentos encontrados. Confira o mês de cada fatura.`);
  } catch (error) {
    if (generation !== inspectGeneration) return;
    selectedFiles = [];
    selectedFilesView.replaceChildren();
    setImportStatus(error.message, true);
  }
}

importFile.addEventListener("change", inspectSelectedFiles);
importForm.elements.pdf_password.addEventListener("change", inspectSelectedFiles);
importForm.addEventListener("input", (event) => {
  if (event.target === importForm.elements.mode) {
    importForm.elements.expense_sign.value = event.target.value === "card" ? "positive" : "negative";
  }
  if (event.target !== importFile) invalidateImportPreview();
});

function importPayload(entry) {
  const mapping = {};
  for (const select of mappingFields.querySelectorAll("select[data-map]")) {
    mapping[select.dataset.map] = select.value === "" ? null : Number(select.value);
  }
  return {
    ...entry.data,
    bank: importForm.elements.bank.value,
    scope: importForm.elements.scope.value,
    mode: importForm.elements.mode.value,
    expense_sign: importForm.elements.expense_sign.value,
    account: importForm.elements.account.value,
    pdf_password: importForm.elements.pdf_password.value,
    statement_month: entry.monthInput ? entry.monthInput.value : undefined,
    mapping,
  };
}

function renderImportPreview(previews) {
  importPreview.replaceChildren();
  for (const preview of previews) {
    const title = document.createElement("strong");
    title.textContent = `${preview.filename} · ${preview.count} lançamentos`;
    importPreview.append(title);
    for (const item of preview.sample.slice(0, 3)) {
      const row = document.createElement("div");
      row.className = "preview-row";
      const details = document.createElement("span");
      details.textContent = `${formatDate(item.occurred_on)} · ${item.description}`;
      const value = document.createElement("b");
      value.textContent = `${item.kind === "expense" ? "Despesa" : "Receita"} · ${money.format(item.amount_cents / 100)}${item.category === "Pagamento de fatura" ? " · saída de caixa" : item.excluded ? " · fora do resumo" : ""}`;
      row.append(details, value);
      importPreview.append(row);
    }
  }
  importPreview.hidden = false;
}

importForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFiles.length) {
    setImportStatus("Escolha arquivos válidos primeiro.", true);
    return;
  }
  const button = document.querySelector("#preview-button");
  button.disabled = true;
  invalidateImportPreview();
  try {
    const previews = [];
    const payloads = [];
    for (const entry of selectedFiles) {
      const payload = importPayload(entry);
      const result = await request("/imports/preview", {
        method: "POST", body: JSON.stringify(payload),
      });
      previews.push({ filename: entry.data.filename, ...result });
      payloads.push(payload);
    }
    readyPayloads = payloads;
    renderImportPreview(previews);
    importButton.disabled = false;
    setImportStatus("Confira a prévia de cada fatura e confirme a importação.");
  } catch (error) {
    setImportStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
});

async function refreshInvoices() {
  const list = document.querySelector("#invoice-list");
  try {
    const result = await request(`/invoices?scope=${encodeURIComponent(document.querySelector("#scope-view").value)}`);
    list.replaceChildren();
    if (!result.items.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Nenhuma fatura registrada ainda.";
      list.append(empty);
      return;
    }
    for (const item of result.items) {
      const card = document.createElement("div");
      card.className = "invoice-card";
      const row = document.createElement("div");
      row.className = "invoice-row";
      const description = document.createElement("span");
      description.textContent = `${item.invoice_month} · ${item.source}${item.scope === "business" ? " · PJ" : ""} · ${item.transaction_count} lançamentos${item.pending_count ? ` · ${item.pending_count} provisórios (fatura aberta)` : ""}`;
      const value = document.createElement("b");
      value.textContent = money.format((item.expense_cents - item.credit_cents) / 100);
      const details = document.createElement("div");
      details.className = "invoice-details";
      details.hidden = true;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "text-button";
      button.textContent = "Ver itens";
      button.addEventListener("click", async () => {
        if (!details.hidden) {
          details.hidden = true;
          button.textContent = "Ver itens";
          return;
        }
        try {
          const path = `/invoices/${item.invoice_month}?source=${encodeURIComponent(item.source)}&scope=${item.scope}`;
          const invoice = await request(path);
          details.replaceChildren();
          for (const transaction of invoice.items) {
            const entry = document.createElement("div");
            entry.className = "invoice-detail-row";
            const name = document.createElement("span");
            name.textContent = `${formatDate(transaction.occurred_on)} · ${transaction.description}`;
            const amount = document.createElement("b");
            amount.textContent = `${transaction.kind === "expense" ? "−" : "+"} ${money.format(transaction.amount_cents / 100)}`;
            entry.append(name, amount);
            details.append(entry);
          }
          details.hidden = false;
          button.textContent = "Ocultar itens";
        } catch (error) {
          showNotice(error.message, true);
        }
      });
      row.append(description, value, button);
      card.append(row, details);
      list.append(card);
    }
  } catch (error) {
    showNotice(error.message, true);
  }
}

importButton.addEventListener("click", async () => {
  if (!readyPayloads.length) return;
  importButton.disabled = true;
  let imported = 0;
  let skipped = 0;
  let matchedTransfers = 0;
  try {
    for (const [index, payload] of readyPayloads.entries()) {
      setImportStatus(`Importando arquivo ${index + 1} de ${readyPayloads.length}...`);
      const result = await request("/imports", {
        method: "POST", body: JSON.stringify(payload),
      });
      imported += result.imported;
      skipped += result.skipped;
      matchedTransfers += result.matched_transfers || 0;
    }
    const scopeView = document.querySelector("#scope-view");
    scopeView.value = readyPayloads[0].scope;
    await Promise.all([refresh(), refreshInvoices()]);
    setImportStatus(`${imported} importados; ${skipped} já existiam.${matchedTransfers ? ` ${matchedTransfers} transferências entre contas vinculadas.` : ""}`);
    showNotice("Importação concluída.");
    invalidateImportPreview();
  } catch (error) {
    setImportStatus(`${error.message} ${imported} lançamentos já foram importados; você pode tentar novamente sem duplicar.`, true);
    importButton.disabled = false;
  }
});

refreshInvoices();
document.addEventListener("finance-scope-change", () => {
  importForm.elements.scope.value = document.querySelector("#scope-view").value === "business" ? "business" : "personal";
  invalidateImportPreview();
  refreshInvoices();
});
