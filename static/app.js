const API = "/api/v1";
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const monthInput = document.querySelector("#month");
const scopeInput = document.querySelector("#scope-view");
const form = document.querySelector("#transaction-form");
const notice = document.querySelector("#notice");
let editingId = null;

function todayLocal() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function showNotice(message, isError = false) {
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.hidden = false;
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => { notice.hidden = true; }, 5000);
}

async function request(path, options = {}) {
  const response = await fetch(API + path, {
    ...options,
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Não foi possível concluir a operação.");
  return payload;
}

function formatDate(isoDate) {
  const [year, month, day] = isoDate.split("-");
  return `${day}/${month}/${year}`;
}

function setSummary(summary) {
  document.querySelector("#balance").textContent = summary.cash_balance_cents === null
    ? "—" : money.format(summary.cash_balance_cents / 100);
  const snapshots = summary.cash_balance_snapshots;
  document.querySelector("#balance-as-of").textContent = snapshots.length === 1
    ? `Extrato de ${formatDate(snapshots[0].as_of)}`
    : snapshots.length > 1 ? `Soma de ${snapshots.length} contas` : "Importe um extrato com saldo";
  document.querySelector("#income").textContent = money.format(summary.income_cents / 100);
  document.querySelector("#expenses").textContent = money.format(summary.expense_cents / 100);
  document.querySelector("#card-payment").textContent = money.format(summary.card_payment_cents / 100);
  const purchasesText = `Compras registradas: ${money.format(summary.card_purchases_cents / 100)}`;
  const latestInvoice = summary.latest_invoice_month;
  document.querySelector("#card-purchases").textContent = latestInvoice
    ? `${purchasesText} · última fatura ${latestInvoice}`
    : `${purchasesText} · sem faturas importadas`;
  const list = document.querySelector("#categories-list");
  list.replaceChildren();
  document.querySelector("#purchase-total").textContent = money.format(summary.purchase_total_cents / 100);
  if (summary.purchase_categories.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhuma compra registrada neste mês.";
    list.append(empty);
    return;
  }
  for (const entry of summary.purchase_categories) {
    const row = document.createElement("div");
    row.className = "category-row";
    const label = document.createElement("div");
    label.className = "category-label";
    const name = document.createElement("span");
    name.textContent = entry.category;
    const amount = document.createElement("span");
    amount.textContent = money.format(entry.amount_cents / 100);
    label.append(name, amount);
    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = "bar-fill";
    fill.style.width = `${Math.round(100 * entry.amount_cents / summary.purchase_total_cents)}%`;
    track.append(fill);
    row.append(label, track);
    list.append(row);
  }
}

function renderTransactions(items) {
  const list = document.querySelector("#transactions-list");
  list.replaceChildren();
  document.querySelector("#count").textContent = `${items.length} ${items.length === 1 ? "lançamento" : "lançamentos"}`;
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Ainda não há lançamentos neste mês. Adicione o primeiro ao lado.";
    list.append(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = `transaction ${item.kind}`;
    const icon = document.createElement("div");
    icon.className = "transaction-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = item.kind === "income" ? "↙" : "↗";
    const main = document.createElement("div");
    main.className = "transaction-main";
    const name = document.createElement("div");
    name.className = "transaction-name";
    name.textContent = item.description;
    const meta = document.createElement("div");
    meta.className = "transaction-meta";
    meta.textContent = `${item.category} · ${formatDate(item.occurred_on)} · ${item.source}${item.scope === "business" ? " · PJ" : ""}${item.invoice_month ? ` · Fatura ${item.invoice_month}` : ""}${item.category === "Pagamento de fatura" ? " · Saída de caixa" : item.excluded ? " · Fora do resumo" : ""}`;
    main.append(name, meta);
    const amount = document.createElement("div");
    amount.className = "transaction-amount";
    amount.textContent = `${item.kind === "income" ? "+" : "−"} ${money.format(item.amount_cents / 100)}`;
    const actions = document.createElement("div");
    actions.className = "row-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Editar";
    edit.setAttribute("aria-label", `Editar ${item.description}`);
    edit.addEventListener("click", () => startEdit(item));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Excluir";
    remove.setAttribute("aria-label", `Excluir ${item.description}`);
    remove.addEventListener("click", () => deleteItem(item));
    actions.append(edit, remove);
    row.append(icon, main, amount, actions);
    list.append(row);
  }
}

async function refresh() {
  try {
    const month = encodeURIComponent(monthInput.value);
    const scope = encodeURIComponent(scopeInput.value);
    const [summary, transactions] = await Promise.all([
      request(`/summary?month=${month}&scope=${scope}`),
      request(`/transactions?month=${month}&scope=${scope}`),
    ]);
    setSummary(summary);
    renderTransactions(transactions.items);
    document.dispatchEvent(new Event("finance-data-change"));
  } catch (error) {
    showNotice(error.message, true);
  }
}

function resetForm() {
  editingId = null;
  form.reset();
  form.elements.scope.value = scopeInput.value === "business" ? "business" : "personal";
  const today = todayLocal();
  form.elements.occurred_on.value = monthInput.value === today.slice(0, 7)
    ? today : `${monthInput.value}-01`;
  document.querySelector("#form-title").textContent = "Novo lançamento";
  document.querySelector("#save-button").textContent = "Salvar lançamento";
  document.querySelector("#cancel-button").hidden = true;
}

function startEdit(item) {
  editingId = item.id;
  form.elements.kind.value = item.kind;
  form.elements.description.value = item.description;
  form.elements.amount.value = (item.amount_cents / 100).toFixed(2).replace(".", ",");
  form.elements.category.value = item.category;
  form.elements.occurred_on.value = item.occurred_on;
  form.elements.excluded.checked = Boolean(item.excluded);
  form.elements.scope.value = item.scope;
  document.querySelector("#form-title").textContent = item.invoice_month
    ? `Editar compra da fatura ${item.invoice_month}` : "Editar lançamento";
  document.querySelector("#save-button").textContent = "Salvar alterações";
  document.querySelector("#cancel-button").hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "center" });
  form.elements.description.focus();
}

async function deleteItem(item) {
  if (!window.confirm(`Excluir o lançamento “${item.description}”?`)) return;
  try {
    await request(`/transactions/${item.id}`, { method: "DELETE" });
    if (editingId === item.id) resetForm();
    await Promise.all([refresh(), refreshInvoices()]);
    showNotice("Lançamento excluído.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#save-button");
  const data = {
    kind: form.elements.kind.value,
    description: form.elements.description.value,
    amount: form.elements.amount.value,
    category: form.elements.category.value,
    occurred_on: form.elements.occurred_on.value,
    excluded: form.elements.excluded.checked,
    scope: form.elements.scope.value,
  };
  button.disabled = true;
  try {
    await request(editingId ? `/transactions/${editingId}` : "/transactions", {
      method: editingId ? "PUT" : "POST",
      body: JSON.stringify(data),
    });
    monthInput.value = data.occurred_on.slice(0, 7);
    scopeInput.value = data.scope;
    resetForm();
    await Promise.all([refresh(), refreshInvoices()]);
    showNotice("Lançamento salvo.");
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#cancel-button").addEventListener("click", resetForm);
monthInput.addEventListener("change", () => { resetForm(); refresh(); });
scopeInput.addEventListener("change", () => { resetForm(); refresh(); document.dispatchEvent(new Event("finance-scope-change")); });
monthInput.value = todayLocal().slice(0, 7);
resetForm();
refresh();
