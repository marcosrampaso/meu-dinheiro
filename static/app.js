const API = "/api/v1";
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const monthInput = document.querySelector("#month");
const scopeInput = document.querySelector("#scope-view");
const purchasePeriodInput = document.querySelector("#purchase-period");
const includeUncategorized = document.querySelector("#include-uncategorized");
const form = document.querySelector("#transaction-form");
const notice = document.querySelector("#notice");
let editingId = null;
const transactionCategoryFilter = document.querySelector("#transaction-category-filter");
let transactionMonthItems = [];

function categoryLabel(category) {
  return category === "Importado" ? "Sem categoria" : category;
}

function visibleCategories(categories) {
  return includeUncategorized.checked ? categories : categories.filter(entry => entry.category !== "Importado");
}

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
  await localAuth.ready();
  const send = () => fetch(API + path, {
    ...options,
    headers: { ...(options.body ? { "Content-Type": "application/json" } : {}),
               ...options.headers, "X-Local-Session": localAuth.token() },
    cache: "no-store",
  });
  let response = await send();
  if (response.status === 401) {
    localAuth.lock();
    await localAuth.ready();
    response = await send();
  }
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
}

function renderPurchaseCategories(data) {
  const list = document.querySelector("#categories-list");
  list.replaceChildren();
  const entries = visibleCategories(data.purchase_categories);
  const shownTotal = entries.reduce((sum, item) => sum + item.amount_cents, 0);
  const unknown = data.purchase_categories.find(entry => entry.category === "Importado")?.amount_cents || 0;
  document.querySelector("#purchase-total").textContent = money.format(shownTotal / 100);
  document.querySelector("#category-coverage").textContent = `Gráfico: ${money.format(shownTotal / 100)}${includeUncategorized.checked ? " em todas as compras" : " em compras categorizadas"}. Total de compras detalhadas: ${money.format(data.purchase_total_cents / 100)} · ${money.format(unknown / 100)} ainda sem categoria.`;
  if (entries.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = data.purchase_categories.length ? "As compras deste período ainda aguardam categorização. Você pode revisar as sugestões abaixo." : "Nenhuma compra registrada neste período.";
    list.append(empty);
    return;
  }
  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = "category-row";
    const label = document.createElement("div");
    label.className = "category-label";
    const name = document.createElement("span");
    name.textContent = categoryLabel(entry.category);
    const amount = document.createElement("span");
    amount.textContent = money.format(entry.amount_cents / 100);
    label.append(name, amount);
    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = "bar-fill";
    fill.style.width = `${Math.round(100 * entry.amount_cents / shownTotal)}%`;
    track.append(fill);
    row.append(label, track);
    list.append(row);
  }
}

function renderTransactions(items) {
  transactionMonthItems = items;
  const selected = transactionCategoryFilter.value;
  transactionCategoryFilter.replaceChildren(new Option("Todas as categorias", ""));
  for (const category of [...new Set(items.map(item => item.category))].sort()) {
    transactionCategoryFilter.add(new Option(categoryLabel(category), category));
  }
  if (selected && ![...transactionCategoryFilter.options].some(option => option.value === selected)) {
    transactionCategoryFilter.add(new Option(categoryLabel(selected), selected));
  }
  transactionCategoryFilter.value = selected;
  const totalCount = items.length;
  if (selected) items = items.filter(item => item.category === selected);
  const list = document.querySelector("#transactions-list");
  list.replaceChildren();
  document.querySelector("#count").textContent = `${items.length} ${items.length === 1 ? "lançamento" : "lançamentos"}${selected ? ` de ${totalCount}` : ""}`;
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = selected ? "Nenhum lançamento desta categoria no mês selecionado." : "Ainda não há lançamentos neste mês. Adicione o primeiro ao lado.";
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
    if (item.category === "Importado") meta.textContent = meta.textContent.replace(/^Importado/, "Sem categoria");
    if (item.pending) meta.textContent += " · Provisório (fatura aberta)";
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
    if (item.kind === "expense" && item.invoice_month === null && !item.excluded && item.category === "Importado") {
      const investment = document.createElement("button");
      investment.type = "button";
      investment.textContent = "É investimento";
      investment.setAttribute("aria-label", `Marcar ${item.description} como investimento`);
      investment.addEventListener("click", () => markInvestment(item));
      actions.append(investment);
    }
    actions.append(edit, remove);
    row.append(icon, main, amount, actions);
    list.append(row);
  }
}

transactionCategoryFilter.addEventListener("change", () => renderTransactions(transactionMonthItems));

async function refresh({ notify = true } = {}) {
  try {
    const month = encodeURIComponent(monthInput.value);
    const scope = encodeURIComponent(scopeInput.value);
    const categoryMonth = purchasePeriodInput.value === "month" ? `&month=${month}` : "";
    const [summary, transactions, categories] = await Promise.all([
      request(`/summary?month=${month}&scope=${scope}`),
      request(`/transactions?month=${month}&scope=${scope}`),
      request(`/purchase-categories?scope=${scope}${categoryMonth}`),
    ]);
    if (month !== encodeURIComponent(monthInput.value) || scope !== encodeURIComponent(scopeInput.value)
        || categoryMonth !== (purchasePeriodInput.value === "month" ? `&month=${month}` : "")) return;
    setSummary(summary);
    renderTransactions(transactions.items);
    renderPurchaseCategories(categories);
    if (notify) document.dispatchEvent(new Event("finance-data-change"));
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

async function markInvestment(item) {
  if (!window.confirm(`Você confirmou no comprovante que “${item.description}” (${money.format(item.amount_cents / 100)}) foi para uma corretora? Esse lançamento sairá dos gastos.`)) return;
  try {
    await request(`/transactions/${item.id}`, {
      method: "PUT",
      body: JSON.stringify({
        kind: item.kind, description: item.description,
        amount: (item.amount_cents / 100).toFixed(2),
        category: "Transferência para investimento",
        occurred_on: item.occurred_on, excluded: true, scope: item.scope,
      }),
    });
    if (editingId === item.id) resetForm();
    await refresh();
    showNotice("Transferência marcada como investimento. O saldo da conta continua vindo do extrato.");
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
purchasePeriodInput.addEventListener("change", () => {
  window.localStorage.setItem("purchase-period", purchasePeriodInput.value);
  refresh({ notify: false });
});
includeUncategorized.checked = window.localStorage.getItem("include-uncategorized") === "true";
includeUncategorized.addEventListener("change", () => {
  window.localStorage.setItem("include-uncategorized", String(includeUncategorized.checked));
  refresh();
});
let lastResumeRefresh = 0;
function refreshOnResume() {
  if (document.hidden || Date.now() - lastResumeRefresh < 1000) return;
  lastResumeRefresh = Date.now();
  refresh({ notify: false });
  document.dispatchEvent(new Event("finance-view-resume"));
}
window.addEventListener("focus", refreshOnResume);
document.addEventListener("visibilitychange", refreshOnResume);
monthInput.value = todayLocal().slice(0, 7);
purchasePeriodInput.value = window.localStorage.getItem("purchase-period") === "all" ? "all" : "month";
resetForm();
refresh();
