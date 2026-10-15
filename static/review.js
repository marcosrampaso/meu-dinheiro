const reviewFilters = document.querySelector("#review-filters");
let reviewData = null;
let reviewOffset = 0;
let reviewMerchant = "";
let reviewRequestId = 0;
let reviewLastLink = "";
const reviewDrafts = new Map();

function reviewNode(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}
function reviewStatus(message, error = false) {
  const node = document.querySelector("#review-status");
  node.textContent = message; node.classList.toggle("error", error);
}
function updateReviewSave() {
  const button = document.querySelector("#review-save");
  button.disabled = reviewDrafts.size === 0 || reviewDrafts.size > 40;
  button.textContent = reviewDrafts.size ? `Salvar ${reviewDrafts.size} ${reviewDrafts.size === 1 ? "alteração" : "alterações"}` : "Salvar alterações";
  if (reviewDrafts.size > 40) reviewStatus("Salve até 40 alterações por vez.", true);
}
function setReviewDraft(item, category) {
  if (category === item.category) reviewDrafts.delete(item.id); else reviewDrafts.set(item.id, category);
  updateReviewSave();
}
function reviewChoices(select, choices, value) {
  select.replaceChildren(...choices.map(category => new Option(categoryLabel(category), category)));
  select.value = value;
}
function renderReview(data) {
  reviewData = data; reviewDrafts.clear(); updateReviewSave();
  const selectedCategory = reviewFilters.elements.category.value;
  reviewFilters.elements.category.replaceChildren(new Option("Todas", ""), ...data.categories.map(category => new Option(categoryLabel(category), category)));
  if (selectedCategory && !data.categories.includes(selectedCategory)) reviewFilters.elements.category.add(new Option(categoryLabel(selectedCategory), selectedCategory));
  reviewFilters.elements.category.value = selectedCategory;
  reviewChoices(document.querySelector("#review-bulk-category"), data.choices, data.choices[0] || "");
  document.querySelector("#review-expenses").textContent = money.format(data.expense_cents / 100);
  document.querySelector("#review-income").textContent = money.format(data.income_cents / 100);
  document.querySelector("#review-total").textContent = data.total;
  document.querySelector("#review-context").textContent = `Resultados: ${data.scope === "all" ? "Tudo" : data.scope === "business" ? "PJ" : "Pessoal"} · ${data.month || "todo o histórico"}${data.category ? ` · ${data.category}` : ""}${data.merchant ? " · estabelecimento selecionado" : ""}`;
  document.querySelector("#review-clear-merchant").hidden = !data.merchant;
  document.querySelector("#review-method").textContent = data.note;
  const months = document.querySelector("#review-monthly"); months.replaceChildren();
  const maximum = Math.max(1, ...data.monthly.map(row => Math.max(row.expense_cents, row.income_cents)));
  for (const row of data.monthly) {
    const line = reviewNode("div", "", "review-month");
    line.append(reviewNode("strong", row.month.split("-").reverse().join("/")));
    const detail = reviewNode("div", "");
    for (const [key, label] of [["expense_cents", "Despesas"], ["income_cents", "Receitas"]]) {
      if (!row[key]) continue;
      const track = reviewNode("div", "", "bar-track");
      const fill = reviewNode("div", "", "bar-fill " + key);
      fill.style.width = `${row[key] / maximum * 100}%`; track.append(fill);
      detail.append(reviewNode("span", `${label}: ${money.format(row[key] / 100)}`), track);
    }
    detail.append(reviewNode("small", `${row.count} lançamentos${row.pending_cents ? ` · ${money.format(row.pending_cents / 100)} provisórios` : ""}`));
    line.append(detail); months.append(line);
  }
  if (!data.monthly.length) months.append(reviewNode("p", "Nenhuma compra ou receita elegível neste filtro.", "chart-note"));
  const merchants = document.querySelector("#review-merchants"); merchants.replaceChildren();
  for (const group of data.merchants) {
    const button = reviewNode("button", "", "review-merchant"); button.type = "button";
    button.append(reviewNode("strong", group.description), reviewNode("span", `${money.format((group.expense_cents + group.income_cents) / 100)} · ${group.count} lançamentos`),
      reviewNode("small", `${group.scope === "business" ? "PJ" : "Pessoal"}${group.installment_count ? ` · ${group.installment_count} com indicação de parcela` : " · sem indicação de parcela na descrição"}`));
    button.addEventListener("click", () => { reviewMerchant = group.key; reviewFilters.elements.scope.value = group.scope; void refreshReview(0); });
    merchants.append(button);
  }
  const list = document.querySelector("#review-list"); list.replaceChildren();
  for (const item of data.items) {
    const row = reviewNode("div", "", "review-row"); row.dataset.id = item.id;
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.disabled = !item.editable;
    checkbox.setAttribute("aria-label", `Selecionar ${item.description}`);
    const detail = reviewNode("div", "", "review-detail");
    detail.append(reviewNode("strong", item.description), reviewNode("small", `${formatDate(item.occurred_on)} · ${item.source} · ${item.scope === "business" ? "PJ" : "Pessoal"}${item.invoice_month ? ` · fatura ${item.invoice_month}` : " · conta"}`),
      reviewNode("small", item.pending ? "Compra pendente · valor provisório" : item.installment_hint ? "Há indicação de parcela na descrição" : "Sem indicação de parcela na descrição"));
    const value = reviewNode("strong", `${item.kind === "income" ? "+" : "−"} ${money.format(item.amount_cents / 100)}`, "review-row-amount");
    const label = reviewNode("label", "Categoria");
    const select = document.createElement("select"); select.disabled = !item.editable;
    reviewChoices(select, [...new Set([...data.choices, item.category])], item.category);
    select.setAttribute("aria-label", `Categoria de ${item.description}`);
    select.addEventListener("change", () => setReviewDraft(item, select.value)); label.append(select);
    if (!item.editable) detail.append(reviewNode("small", "Movimentação protegida · fora da edição de categorias"));
    row.append(checkbox, detail, value, label); list.append(row);
  }
  if (!data.items.length) list.append(reviewNode("p", "Nenhum lançamento encontrado. Ajuste os filtros.", "chart-note"));
  document.querySelector("#review-page").textContent = data.total ? `${data.offset + 1}–${data.offset + data.items.length} de ${data.total}` : "Sem resultados";
  document.querySelector("#review-previous").disabled = data.offset === 0;
  document.querySelector("#review-next").disabled = data.offset + data.limit >= data.total;
}
async function refreshReview(offset = reviewOffset) {
  if (reviewView.hidden) return;
  if (reviewDrafts.size && !window.confirm("Há categorias alteradas sem salvar. Descartar o rascunho e atualizar a lista?")) {
    if (reviewData) {
      const fields = reviewFilters.elements;
      for (const key of ["scope", "kind", "payment_type", "category", "search"]) fields[key].value = reviewData[key];
      fields.range.value = reviewData.month ? "month" : "all";
      fields.month.value = reviewData.month || todayLocal().slice(0, 7);
      fields.month.disabled = !reviewData.month;
      fields.include_excluded.checked = reviewData.include_excluded;
      reviewMerchant = reviewData.merchant;
    }
    return;
  }
  const fields = reviewFilters.elements;
  const params = new URLSearchParams({ scope: fields.scope.value, kind: fields.kind.value,
    payment_type: fields.payment_type.value, category: fields.category.value, search: fields.search.value,
    include_excluded: fields.include_excluded.checked, offset, merchant: reviewMerchant });
  if (fields.range.value === "month") params.set("month", fields.month.value);
  const requestId = ++reviewRequestId;
  reviewStatus("Consultando os lançamentos registrados…");
  try {
    const data = await request(`/transactions/review?${params}`);
    if (requestId !== reviewRequestId || reviewView.hidden) return;
    reviewOffset = data.offset; renderReview(data); reviewStatus("Confira as categorias antes de salvar. Nenhuma correção é automática.");
  } catch (error) { if (requestId === reviewRequestId) reviewStatus(error.message, true); }
}
reviewFilters.elements.month.value = todayLocal().slice(0, 7);
reviewFilters.elements.range.addEventListener("change", () => { reviewFilters.elements.month.disabled = reviewFilters.elements.range.value !== "month"; });
reviewFilters.addEventListener("submit", event => { event.preventDefault(); reviewMerchant = ""; void refreshReview(0); });
document.querySelector("#review-refresh").addEventListener("click", () => refreshReview());
document.querySelector("#review-clear-merchant").addEventListener("click", () => { reviewMerchant = ""; void refreshReview(0); });
document.querySelector("#review-previous").addEventListener("click", () => refreshReview(Math.max(0, reviewOffset - 50)));
document.querySelector("#review-next").addEventListener("click", () => refreshReview(reviewOffset + 50));
document.querySelector("#review-select-page").addEventListener("click", () => {
  let count = 0;
  for (const checkbox of document.querySelectorAll('#review-list input[type="checkbox"]')) checkbox.checked = !checkbox.disabled && count++ < 40;
});
document.querySelector("#review-fill").addEventListener("click", () => {
  if (!reviewData) return;
  const category = document.querySelector("#review-bulk-category").value;
  for (const row of document.querySelectorAll("#review-list .review-row")) {
    if (!row.querySelector("input").checked) continue;
    const item = reviewData.items.find(item => item.id === Number(row.dataset.id));
    row.querySelector("select").value = category; setReviewDraft(item, category);
  }
});
document.querySelector("#review-save").addEventListener("click", async () => {
  if (!reviewData || !reviewDrafts.size || reviewDrafts.size > 40) return;
  const button = document.querySelector("#review-save"); button.disabled = true;
  const items = [...reviewDrafts].map(([id, category]) => ({ id, category, signature: reviewData.items.find(item => item.id === id).signature }));
  const scope = reviewData.scope;
  reviewFilters.inert = true;
  document.querySelector("#review-list").inert = true;
  try {
    const result = await request("/transactions/review", { method: "POST", body: JSON.stringify({ scope, items,
      remember: document.querySelector("#review-remember").checked }) });
    reviewDrafts.clear();
    document.dispatchEvent(new Event("finance-data-change"));
    await refreshReview();
    reviewStatus(`${result.applied} categorias corrigidas · ${result.learned} regras de estabelecimento guardadas para novas compras.`);
  } catch (error) { reviewStatus(error.message, true); updateReviewSave(); }
  finally { reviewFilters.inert = false; document.querySelector("#review-list").inert = false; }
});
function resumeReview() {
  const link = window.location.hash;
  if (link.includes("?") && link !== reviewLastLink) {
    const params = new URLSearchParams(link.split("?")[1]);
    const scope = params.get("scope");
    if (["personal", "business", "all"].includes(scope)) reviewFilters.elements.scope.value = scope;
    const category = params.get("category") || "";
    if (category && ![...reviewFilters.elements.category.options].some(option => option.value === category)) reviewFilters.elements.category.add(new Option(category, category));
    reviewFilters.elements.category.value = category;
    reviewMerchant = ""; reviewOffset = 0;
  }
  reviewLastLink = link;
  void refreshReview();
}
document.addEventListener("review-view-resume", resumeReview);
document.addEventListener("finance-data-change", () => { if (!reviewView.hidden) reviewStatus("Os dados mudaram. Atualize a lista antes de continuar a revisão."); });
if (!reviewView.hidden) resumeReview();
