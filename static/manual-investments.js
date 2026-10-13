const manualFlowForm = document.querySelector("#manual-flow-form");
const manualSnapshotForm = document.querySelector("#manual-snapshot-form");
let editingManualFlowId = null;
let editingManualSnapshotDate = null;
let manualInvestmentRequestId = 0;

function manualStatus(id, message, error = false) {
  const target = document.getElementById(id);
  target.textContent = message;
  target.classList.toggle("error", error);
}

function amountInput(cents) {
  return (cents / 100).toFixed(2).replace(".", ",");
}

function cancelManualFlowEdit() {
  editingManualFlowId = null;
  manualFlowForm.reset();
  manualFlowForm.elements.occurred_on.value = todayLocal();
  manualFlowForm.elements.asset_id.value = "";
  manualFlowForm.elements.external.checked = true;
  manualFlowForm.querySelector('button[type="submit"]').textContent = "Adicionar";
  manualFlowForm.querySelector("[data-cancel]").hidden = true;
}

function cancelManualSnapshotEdit() {
  editingManualSnapshotDate = null;
  manualSnapshotForm.reset();
  manualSnapshotForm.elements.as_of.readOnly = false;
  manualSnapshotForm.elements.as_of.value = todayLocal();
  manualSnapshotForm.querySelector('button[type="submit"]').textContent = "Salvar saldo";
  manualSnapshotForm.querySelector("[data-cancel]").hidden = true;
}

function manualRow(labelText, onEdit, onDelete, warning = "") {
  const row = document.createElement("div");
  row.className = "manual-investment-row";
  const label = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = labelText;
  label.append(strong);
  if (warning) {
    const note = document.createElement("small");
    note.textContent = warning;
    label.append(note);
  }
  const actions = document.createElement("div");
  actions.className = "manual-investment-actions";
  for (const [text, handler] of [["Editar", onEdit], ["Excluir", onDelete]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", handler);
    actions.append(button);
  }
  row.append(label, actions);
  return row;
}

function renderManualInvestments(data) {
  const assetSelect = manualFlowForm.elements.asset_id;
  const selectedAsset = assetSelect.value;
  assetSelect.replaceChildren();
  const cashOption = document.createElement("option");
  cashOption.value = "";
  cashOption.textContent = "Saldo da XP · sem ativo";
  assetSelect.append(cashOption);
  for (const asset of data.assets) {
    const option = document.createElement("option");
    option.value = String(asset.id);
    option.textContent = `${asset.name} · ${asset.category}`;
    assetSelect.append(option);
  }
  if (selectedAsset && ![...assetSelect.options].some(option => option.value === selectedAsset)) {
    const historical = data.flows.find(flow => String(flow.asset_id) === selectedAsset);
    if (historical) {
      const option = document.createElement("option");
      option.value = selectedAsset;
      option.textContent = `${historical.asset_name} · fora da posição atual`;
      assetSelect.append(option);
    }
  }
  assetSelect.value = selectedAsset && [...assetSelect.options].some(option => option.value === selectedAsset)
    ? selectedAsset : "";
  const flows = document.querySelector("#manual-flow-list");
  flows.replaceChildren();
  if (!data.flows.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhum aporte ou resgate manual registrado.";
    flows.append(empty);
  }
  for (const flow of data.flows) {
    flows.append(manualRow(
      `${flow.kind === "deposit" ? "Aporte" : "Resgate"} · ${flow.asset_name || "Saldo da XP"} · ${formatDate(flow.occurred_on)} · ${money.format(flow.amount_cents / 100)}`,
      () => {
        editingManualFlowId = flow.id;
        manualFlowForm.elements.occurred_on.value = flow.occurred_on;
        manualFlowForm.elements.kind.value = flow.kind;
        if (flow.asset_id && ![...assetSelect.options].some(option => option.value === String(flow.asset_id))) {
          const option = document.createElement("option");
          option.value = String(flow.asset_id);
          option.textContent = `${flow.asset_name} · fora da posição atual`;
          assetSelect.append(option);
        }
        assetSelect.value = flow.asset_id === null ? "" : String(flow.asset_id);
        manualFlowForm.elements.external.checked = flow.external;
        manualFlowForm.elements.amount.value = amountInput(flow.amount_cents);
        manualFlowForm.querySelector('button[type="submit"]').textContent = "Salvar edição";
        manualFlowForm.querySelector("[data-cancel]").hidden = false;
        manualFlowForm.scrollIntoView({ behavior: "smooth", block: "center" });
      },
      async () => {
        try {
          await request(`/investments/manual/flows/${flow.id}`, { method: "DELETE" });
          if (editingManualFlowId === flow.id) cancelManualFlowEdit();
          document.dispatchEvent(new Event("investment-data-change"));
          manualStatus("manual-flow-status", "Registro excluído.");
        } catch (error) { manualStatus("manual-flow-status", error.message, true); }
      },
      flow.matched_import ? "Já aparece no CSV da XP; contado uma vez." :
        flow.external ? "Dinheiro entrou ou saiu da XP." : "Aplicação ou resgate dentro da XP; não soma novo dinheiro."
    ));
  }
  const totals = document.querySelector("#manual-asset-totals");
  totals.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = "Histórico por investimento";
  totals.append(heading);
  if (!data.assets.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Importe uma posição detalhada da XP para preencher a lista de investimentos.";
    totals.append(empty);
  }
  for (const asset of data.asset_totals) {
    const row = document.createElement("div");
    row.className = "manual-investment-row";
    const name = document.createElement("strong");
    name.textContent = asset.name;
    const amount = document.createElement("span");
    amount.textContent = `${money.format(asset.deposited_cents / 100)} aplicado · ${money.format(asset.withdrawn_cents / 100)} resgatado`;
    row.append(name, amount);
    totals.append(row);
  }
  const snapshots = document.querySelector("#manual-snapshot-list");
  snapshots.replaceChildren();
  if (!data.snapshots.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhum saldo manual registrado.";
    snapshots.append(empty);
  }
  for (const snapshot of data.snapshots) {
    snapshots.append(manualRow(
      `${formatDate(snapshot.as_of)} · ${money.format(snapshot.total_cents / 100)}`,
      () => {
        editingManualSnapshotDate = snapshot.as_of;
        manualSnapshotForm.elements.as_of.value = snapshot.as_of;
        manualSnapshotForm.elements.as_of.readOnly = true;
        manualSnapshotForm.elements.total.value = amountInput(snapshot.total_cents);
        manualSnapshotForm.querySelector('button[type="submit"]').textContent = "Salvar edição";
        manualSnapshotForm.querySelector("[data-cancel]").hidden = false;
        manualSnapshotForm.scrollIntoView({ behavior: "smooth", block: "center" });
      },
      async () => {
        try {
          await request(`/investments/manual/snapshots/${snapshot.as_of}`, { method: "DELETE" });
          if (editingManualSnapshotDate === snapshot.as_of) cancelManualSnapshotEdit();
          document.dispatchEvent(new Event("investment-data-change"));
          manualStatus("manual-snapshot-status", "Saldo excluído.");
        } catch (error) { manualStatus("manual-snapshot-status", error.message, true); }
      }
    ));
  }
}

async function refreshManualInvestments() {
  if (investmentsView.hidden) return;
  const requestId = ++manualInvestmentRequestId;
  try {
    const data = await request("/investments/manual");
    if (requestId === manualInvestmentRequestId) renderManualInvestments(data);
  } catch (error) {
    manualStatus("manual-flow-status", error.message, true);
  }
}

manualFlowForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = manualFlowForm.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const path = editingManualFlowId === null ? "/investments/manual/flows" :
      `/investments/manual/flows/${editingManualFlowId}`;
    await request(path, { method: editingManualFlowId === null ? "POST" : "PUT", body: JSON.stringify({
      occurred_on: manualFlowForm.elements.occurred_on.value,
      kind: manualFlowForm.elements.kind.value,
      amount: manualFlowForm.elements.amount.value,
      asset_id: manualFlowForm.elements.asset_id.value ? Number(manualFlowForm.elements.asset_id.value) : null,
      external: manualFlowForm.elements.external.checked,
    }) });
    cancelManualFlowEdit();
    manualStatus("manual-flow-status", "Aporte ou resgate salvo.");
    document.dispatchEvent(new Event("investment-data-change"));
  } catch (error) { manualStatus("manual-flow-status", error.message, true); }
  finally { button.disabled = false; }
});

manualSnapshotForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = manualSnapshotForm.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await request("/investments/manual/snapshots", { method: "POST", body: JSON.stringify({
      as_of: editingManualSnapshotDate || manualSnapshotForm.elements.as_of.value,
      total: manualSnapshotForm.elements.total.value,
    }) });
    cancelManualSnapshotEdit();
    manualStatus("manual-snapshot-status", "Saldo da carteira salvo.");
    document.dispatchEvent(new Event("investment-data-change"));
  } catch (error) { manualStatus("manual-snapshot-status", error.message, true); }
  finally { button.disabled = false; }
});

manualFlowForm.querySelector("[data-cancel]").addEventListener("click", cancelManualFlowEdit);
manualFlowForm.elements.asset_id.addEventListener("change", () => {
  manualFlowForm.elements.external.checked = !manualFlowForm.elements.asset_id.value;
});
manualSnapshotForm.querySelector("[data-cancel]").addEventListener("click", cancelManualSnapshotEdit);
cancelManualFlowEdit();
cancelManualSnapshotEdit();
document.addEventListener("investments-view-resume", refreshManualInvestments);
document.addEventListener("investment-data-change", refreshManualInvestments);
if (!investmentsView.hidden) refreshManualInvestments();
