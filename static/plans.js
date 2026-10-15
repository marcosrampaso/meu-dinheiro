const reservePlanForm = document.querySelector("#reserve-plan-form");
const reservePlanScope = document.querySelector("#plan-scope");
let reservePlanDirty = false;
let reservePlanRequestId = 0;
let reservePlanPreviewTimer = null;
let reservePlanContributionDay = null;

function reservePlanStatus(message, error = false) {
  const target = document.querySelector("#plan-status");
  target.textContent = message;
  target.classList.toggle("error", error);
}

function reservePlanPayload() {
  const fields = reservePlanForm.elements;
  return { scope: reservePlanScope.value, name: fields.name.value,
    essential_expenses: fields.essential_expenses.value,
    target_months: Number(fields.target_months.value), monthly_contribution: fields.monthly_contribution.value,
    reserved_balance: fields.reserved_balance.value, balance_as_of: fields.balance_as_of.value,
    next_contribution_on: fields.next_contribution_on.value,
    contribution_day: reservePlanContributionDay || Number(fields.next_contribution_on.value.slice(8)) };
}

function fillReservePlan(data) {
  const fields = reservePlanForm.elements;
  reservePlanForm.reset();
  fields.balance_as_of.value = todayLocal();
  fields.balance_as_of.max = todayLocal();
  fields.next_contribution_on.value = todayLocal();
  fields.next_contribution_on.min = todayLocal();
  reservePlanContributionDay = Number(todayLocal().slice(8));
  if (data.plan) {
    const plan = data.plan;
    const inputAmount = value => (value / 100).toFixed(2).replace(".", ",");
    fields.name.value = plan.name;
    fields.essential_expenses.value = inputAmount(plan.essential_cents);
    if (![...fields.target_months.options].some(option => option.value === String(plan.target_months))) {
      fields.target_months.add(new Option(`${plan.target_months} meses`, String(plan.target_months)));
    }
    fields.target_months.value = String(plan.target_months);
    fields.monthly_contribution.value = inputAmount(plan.monthly_cents);
    fields.reserved_balance.value = inputAmount(plan.reserved_cents);
    fields.balance_as_of.value = plan.balance_as_of;
    fields.next_contribution_on.value = data.projection.next_contribution_on;
    reservePlanContributionDay = plan.contribution_day;
  }
  document.querySelector("#plan-save").textContent = data.configured ? "Salvar ajustes do plano" : "Criar meu plano";
  document.querySelector("#plan-delete").hidden = !data.configured;
  reservePlanDirty = false;
  document.dispatchEvent(new Event("reserve-plan-loaded"));
}

function drawReserveProjection(data) {
  const target = document.querySelector("#reserve-projection-chart");
  const points = data.points;
  const maximum = Math.max(1, data.target_cents, ...points.map(point => point.balance_cents)) * 1.14;
  const svg = chartSvg("Saldo projetado da reserva de emergência");
  drawFrame(svg, maximum);
  const x = index => points.length === 1 ? 78 : 78 + index / (points.length - 1) * 542;
  const y = amount => 205 - amount / maximum * 177;
  const line = points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.balance_cents)}`).join(" ");
  svg.append(svgNode("path", { d: line + ` L${x(points.length - 1)},205 L78,205 Z`, fill: "#8c7be914" }));
  svg.append(svgNode("line", { x1: 78, x2: 620, y1: y(data.target_cents), y2: y(data.target_cents), stroke: "#18a47e", "stroke-dasharray": "5 5" }));
  svg.append(svgNode("path", { d: line, fill: "none", stroke: "#8470e8", "stroke-width": 3.5, "stroke-linecap": "round" }));
  points.forEach((point, index) => {
    const marker = svgNode("circle", { cx: x(index), cy: y(point.balance_cents), r: 4.5, fill: "#8470e8", stroke: "white", "stroke-width": 2 });
    const message = `${formatDate(point.on)} · ${index ? "Saldo projetado" : "Saldo informado"}: ${money.format(point.balance_cents / 100)}${index ? ` · aporte planejado ${money.format(point.planned_cents / 100)}` : ""}`;
    interactiveMark(marker, message, "reserve-projection-insight");
    marker.append(svgNode("title", {}, message));
    svg.append(marker);
  });
  svg.append(svgNode("text", { x: 78, y: 234, class: "chart-axis-text" }, formatDate(points[0].on)));
  svg.append(svgNode("text", { x: 620, y: 234, "text-anchor": "end", class: "chart-axis-text" }, formatDate(points.at(-1).on)));
  target.replaceChildren(svg);
  chartInsight("reserve-projection-insight", data.projection_truncated
    ? "Mostrando os próximos 24 aportes. A estimativa de conclusão considera todos os aportes necessários."
    : "Selecione um ponto para conferir o saldo projetado. A linha verde representa a meta.");
}

function renderReservePlan(data, preview = false) {
  const configured = data.configured;
  document.querySelector("#plan-empty").hidden = configured;
  for (const id of ["plan-progress", "plan-projection", "plan-cash-check"]) document.getElementById(id).hidden = !configured;
  document.querySelector("#plan-save-state").textContent = preview ? "Simulação não salva" : configured ? "Plano salvo" : "Novo plano";
  if (!configured) return;
  const { plan, projection, cash_check: cash } = data;
  const number = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
  document.querySelector("#plan-title").textContent = plan.name;
  document.querySelector("#plan-balance-date").textContent = `Saldo informado por você em ${formatDate(plan.balance_as_of)}${projection.balance_stale ? " · confirme se ainda está atualizado" : ""}`;
  document.querySelector("#plan-progress-state").textContent = projection.status === "complete" ? "Meta atingida" : projection.status === "paused" ? "Aportes pausados" : "Construindo sua reserva";
  document.querySelector("#plan-current").textContent = money.format(plan.reserved_cents / 100);
  document.querySelector("#plan-target").textContent = money.format(projection.target_cents / 100);
  document.querySelector("#plan-progress-fill").style.width = `${projection.progress_percent}%`;
  document.querySelector("#plan-progress-caption").textContent = `${number.format(projection.progress_percent)}% da meta · proteção desejada: ${plan.target_months} meses`;
  document.querySelector("#plan-remaining").textContent = money.format(projection.remaining_cents / 100);
  document.querySelector("#plan-covered").textContent = `${number.format(projection.months_covered)} meses`;
  document.querySelector("#plan-payments").textContent = projection.contributions_needed === null ? "Aportes pausados" : `${projection.contributions_needed} aportes`;
  document.querySelector("#plan-completion").textContent = projection.status === "complete" ? "Meta atingida" : projection.completion_on ? formatDate(projection.completion_on) : "Sem previsão de data";
  document.querySelector("#plan-projection-note").textContent = projection.note;
  const milestones = document.querySelector("#plan-milestones");
  milestones.replaceChildren();
  for (const milestone of projection.milestones) {
    const item = document.createElement("div");
    item.className = "reserve-milestone" + (milestone.achieved ? " achieved" : "");
    const name = document.createElement("strong");
    name.textContent = `${milestone.achieved ? "✓ " : ""}${milestone.months} ${milestone.months === 1 ? "mês" : "meses"} de proteção`;
    const value = document.createElement("span");
    value.textContent = money.format(milestone.target_cents / 100);
    item.append(name, value);
    milestones.append(item);
  }
  drawReserveProjection(projection);
  const state = document.querySelector("#plan-cash-state");
  state.classList.toggle("error", cash.status === "negative_projection");
  state.textContent = cash.status === "missing_balance" ? "Falta um saldo de conta para conferir os novos aportes." :
    cash.status === "negative_projection" ? "Neste cenário parcial, o caixa ficaria negativo. Revise os compromissos e o aporte planejado." :
    "Confira o cenário antes de aportar. Saldo positivo nesta simulação não confirma dinheiro livre.";
  const values = document.querySelector("#plan-cash-values");
  values.textContent = cash.minimum_balance_cents === null ? "Importe um extrato de conta na área escolhida." :
    `Menor saldo previsto: ${money.format(cash.minimum_balance_cents / 100)} · Saldo final previsto: ${money.format(cash.ending_balance_cents / 100)}`;
  document.querySelector("#plan-cash-note").textContent = `${cash.opening_balance_as_of ? `Saldo base de ${formatDate(cash.opening_balance_as_of)}${cash.balance_stale ? " (pode estar desatualizado)" : ""}. ` : ""}${cash.simulated_contributions} aporte(s) simulado(s) até ${formatDate(cash.through)}. ${cash.partial_invoices ? "Há faturas parciais neste cenário. " : ""}${cash.note}`;
}

async function previewReservePlan(showValidation = false) {
  window.clearTimeout(reservePlanPreviewTimer);
  if (!reservePlanForm.checkValidity()) {
    if (showValidation) reservePlanForm.reportValidity();
    return;
  }
  const requestId = ++reservePlanRequestId;
  const payload = reservePlanPayload();
  try {
    const data = await request("/plans/reserve/preview", { method: "POST", body: JSON.stringify(payload) });
    if (requestId !== reservePlanRequestId || payload.scope !== reservePlanScope.value) return;
    renderReservePlan(data, true);
    reservePlanStatus("Simulação pronta. Salve o plano para guardar seus ajustes.");
  } catch (error) {
    if (requestId === reservePlanRequestId) {
      renderReservePlan({ configured: false }, true);
      reservePlanStatus(error.message, true);
    }
  }
}

async function refreshReservePlan() {
  if (plansView.hidden) return;
  if (reservePlanDirty) { void previewReservePlan(); return; }
  const requestId = ++reservePlanRequestId;
  const scope = reservePlanScope.value;
  try {
    const data = await request(`/plans/reserve?scope=${scope}`);
    if (requestId !== reservePlanRequestId || scope !== reservePlanScope.value) return;
    fillReservePlan(data);
    renderReservePlan(data);
    reservePlanStatus("");
  } catch (error) {
    if (requestId === reservePlanRequestId) reservePlanStatus(error.message, true);
  }
}

reservePlanForm.addEventListener("input", event => {
  if (event.target.name === "next_contribution_on") reservePlanContributionDay = Number(event.target.value.slice(8));
  reservePlanDirty = true;
  reservePlanRequestId += 1;
  document.querySelector("#plan-save-state").textContent = "Ajustes não salvos";
  if (!reservePlanForm.checkValidity()) renderReservePlan({ configured: false }, true);
  window.clearTimeout(reservePlanPreviewTimer);
  reservePlanPreviewTimer = window.setTimeout(() => previewReservePlan(), 450);
});
document.querySelector("#plan-preview").addEventListener("click", () => previewReservePlan(true));
reservePlanForm.addEventListener("submit", async event => {
  event.preventDefault();
  window.clearTimeout(reservePlanPreviewTimer);
  const requestId = ++reservePlanRequestId;
  const payload = reservePlanPayload();
  const button = document.querySelector("#plan-save");
  button.disabled = true;
  try {
    const data = await request("/plans/reserve", { method: "POST", body: JSON.stringify(payload) });
    if (requestId !== reservePlanRequestId || payload.scope !== reservePlanScope.value) return;
    fillReservePlan(data);
    renderReservePlan(data);
    reservePlanStatus("Plano salvo. Atualize o saldo reservado quando ele mudar; nenhuma transferência foi executada.");
  } catch (error) {
    if (requestId === reservePlanRequestId) reservePlanStatus(error.message, true);
  } finally { button.disabled = false; }
});
reservePlanScope.addEventListener("change", () => {
  reservePlanDirty = false;
  reservePlanRequestId += 1;
  window.clearTimeout(reservePlanPreviewTimer);
  fillReservePlan({ configured: false, plan: null });
  renderReservePlan({ configured: false });
  void refreshReservePlan();
});
document.querySelector("#plan-delete").addEventListener("click", async () => {
  const scope = reservePlanScope.value;
  window.clearTimeout(reservePlanPreviewTimer);
  const requestId = ++reservePlanRequestId;
  try {
    await request(`/plans/reserve?scope=${scope}`, { method: "DELETE" });
    if (scope !== reservePlanScope.value || requestId !== reservePlanRequestId) return;
    reservePlanDirty = false;
    await refreshReservePlan();
    reservePlanStatus("Plano excluído. Os lançamentos financeiros e os investimentos continuam registrados.");
  } catch (error) { reservePlanStatus(error.message, true); }
});
document.addEventListener("plans-view-resume", refreshReservePlan);
document.addEventListener("finance-data-change", refreshReservePlan);
if (!plansView.hidden) void refreshReservePlan();

// Historical suggestions stay separate from the saved reserve until explicitly applied.
const planHistoryForm = document.querySelector("#plan-history-form");
let planHistoryAnalysis = null;
let planHistoryCalculation = null;
let planHistoryRequestId = 0;
let planHistoryPreviewTimer = null;

function planHistoryStatus(message, error = false, preview = false) {
  const target = document.querySelector(preview ? "#history-preview-status" : "#plan-history-status");
  target.textContent = message;
  target.classList.toggle("error", error);
}

function planHistoryPayload() {
  const roles = {};
  for (const select of document.querySelectorAll("#history-categories select")) roles[select.dataset.category] = select.value;
  return { scope: reservePlanScope.value, months: Number(document.querySelector("#plan-history-window").value),
    signature: planHistoryAnalysis.signature, roles,
    monthly_income: document.querySelector("#history-confirmed-income").value,
    buffer_percent: Number(document.querySelector("#history-buffer").value),
    target_months: Number(reservePlanForm.elements.target_months.value),
    reserved_balance: reservePlanForm.elements.reserved_balance.value || "0",
    acknowledged: document.querySelector("#history-acknowledged").checked };
}

function planHistoryButtons() {
  const ready = Boolean(planHistoryCalculation && planHistoryCalculation.essential_cents > 0 &&
    document.querySelector("#history-acknowledged").checked);
  document.querySelector("#history-apply-base").disabled = !ready;
  for (const button of document.querySelectorAll("#history-scenarios button")) {
    button.disabled = !ready || planHistoryCalculation.income_cents === null;
  }
}

function planHistoryElement(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function renderPlanHistoryCalculation(result) {
  planHistoryCalculation = result;
  document.querySelector("#history-essential").textContent = `Essenciais: ${money.format(result.essential_cents / 100)} / mês`;
  document.querySelector("#history-flexible").textContent = `Flexíveis: ${money.format(result.flexible_cents / 100)} / mês`;
  document.querySelector("#history-unresolved").textContent = `A revisar: ${money.format(result.unresolved_cents / 100)} / mês`;
  for (const select of document.querySelectorAll("#history-categories select")) select.closest(".history-category").className = `history-category ${select.value}`;
  document.querySelector("#history-scenarios-note").textContent = result.note;
  const cards = document.querySelector("#history-scenarios");
  cards.replaceChildren();
  for (const scenario of result.scenarios) {
    const card = planHistoryElement("article", "", "history-scenario");
    card.append(planHistoryElement("h4", scenario.title));
    card.append(planHistoryElement("strong", scenario.contribution_cents === null ? "Confirme a renda" : money.format(scenario.contribution_cents / 100)));
    card.append(planHistoryElement("p", "Aporte mensal possível neste cenário"));
    card.append(planHistoryElement("p", scenario.reduction_percent
      ? `Reduzir ${scenario.reduction_percent}% dos flexíveis: economia de ${money.format(scenario.saving_cents / 100)} / mês.`
      : "Manter a média de todos os gastos observados."));
    if (scenario.margin_cents !== null) card.append(planHistoryElement("p", `Margem separada para imprevistos: ${money.format(scenario.margin_cents / 100)} / mês.`));
    if (scenario.surplus_cents !== null && scenario.surplus_cents < 0) card.append(planHistoryElement("p", `Faltariam ${money.format(-scenario.surplus_cents / 100)} para cobrir os gastos e a margem. Revise o cenário.`));
    const duration = planHistoryElement("p", result.essential_cents === 0 ? "Revise os essenciais para estimar a meta." :
      scenario.contributions_needed === null ? "Sem previsão de conclusão com este aporte." : `${scenario.contributions_needed} aportes até a meta, sem supor rendimentos.`);
    duration.setAttribute("data-private-value", "");
    card.append(duration);
    const button = planHistoryElement("button", "Usar base e este aporte", "secondary-button");
    button.type = "button";
    button.dataset.scenario = scenario.id;
    button.addEventListener("click", () => applyPlanHistory(scenario.id));
    card.append(button);
    cards.append(card);
  }
  planHistoryButtons();
}

async function previewPlanHistory() {
  window.clearTimeout(planHistoryPreviewTimer);
  if (!planHistoryAnalysis || planHistoryForm.hidden || !planHistoryForm.checkValidity()) return;
  const requestId = ++planHistoryRequestId;
  const payload = planHistoryPayload();
  planHistoryCalculation = null;
  planHistoryButtons();
  try {
    const result = await request("/plans/history/preview", { method: "POST", body: JSON.stringify(payload) });
    if (requestId !== planHistoryRequestId || payload.scope !== reservePlanScope.value) return;
    renderPlanHistoryCalculation(result);
    planHistoryStatus(result.income_cents === null ? "Confirme a renda para comparar os aportes. Você já pode revisar os essenciais." :
      "Cenários atualizados. Revise a base antes de aplicar ao plano.", false, true);
  } catch (error) {
    if (requestId !== planHistoryRequestId) return;
    document.querySelector("#history-scenarios").replaceChildren();
    for (const id of ["history-essential", "history-flexible", "history-unresolved"]) document.getElementById(id).textContent = "—";
    planHistoryStatus(error.message, true, true);
  }
}

async function refreshPlanHistory() {
  if (plansView.hidden) return;
  window.clearTimeout(planHistoryPreviewTimer);
  const requestId = ++planHistoryRequestId;
  const scope = reservePlanScope.value;
  const months = Number(document.querySelector("#plan-history-window").value);
  planHistoryAnalysis = null;
  planHistoryCalculation = null;
  document.querySelector("#plan-history-content").hidden = true;
  planHistoryButtons();
  planHistoryStatus("Analisando os gastos registrados neste Mac…");
  try {
    const data = await request(`/plans/history?scope=${scope}&months=${months}`);
    if (requestId !== planHistoryRequestId || scope !== reservePlanScope.value) return;
    planHistoryAnalysis = data;
    const amountInput = cents => cents === null ? "" : (cents / 100).toFixed(2).replace(".", ",");
    document.querySelector("#history-average").textContent = data.average_spending_cents === null ? "Sem base" : money.format(data.average_spending_cents / 100);
    document.querySelector("#history-income").textContent = data.observed_income_cents === null ? "Sem base" : money.format(data.observed_income_cents / 100);
    document.querySelector("#history-base").textContent = `${data.base_months.length} de ${months} meses · ${data.quality === "documented" ? "cobertura documentada" : "base provisória"}`;
    document.querySelector("#history-months").replaceChildren(...data.monthly.map(month => {
      const chip = planHistoryElement("div", "", `history-month-chip ${month.coverage}`);
      chip.append(planHistoryElement("strong", month.month.split("-").reverse().join("/")), planHistoryElement("span",
        month.coverage === "documented" ? "Cobertura documentada" : month.coverage === "partial" ? "Cobertura parcial" : "Sem cobertura"));
      const details = [...month.accounts.map(row => `${row.source}: ${row.covered_days} de ${row.expected_days} dias de extrato`),
        ...month.cards.map(row => `${row.source}: ${row.registered ? "fatura registrada" : "fatura ausente"}`),
        ...(month.pending_count ? [`${month.pending_count} lançamentos pendentes`] : [])];
      chip.title = details.join(" · ");
      for (const detail of details) chip.append(planHistoryElement("small", detail));
      return chip;
    }));
    document.querySelector("#history-quality-summary").textContent = data.quality === "documented" ? "Conferir cobertura e limites da análise" : "Base provisória: confira o que falta no histórico";
    document.querySelector("#history-quality").open = data.quality !== "documented";
    document.querySelector("#history-warnings").replaceChildren(...data.warnings.map(text => planHistoryElement("li", text)));
    document.querySelector("#history-method").textContent = `${data.note} Meses usados: ${data.base_months.join(", ") || "nenhum"}. Meses sem dados não viram zero.`;
    const categories = document.querySelector("#history-categories");
    categories.replaceChildren();
    const max = Math.max(1, ...data.categories.map(row => row.average_cents));
    for (const [index, row] of data.categories.entries()) {
      const item = planHistoryElement("div", "", `history-category ${row.role}`);
      const name = planHistoryElement("div", "");
      name.append(planHistoryElement("strong", row.category), planHistoryElement("small", row.reviewed ? "Sua última revisão" : "Papel sugerido · confirme"));
      const reviewLink = planHistoryElement("a", "Revisar lançamentos");
      reviewLink.href = `#classificacao?${new URLSearchParams({ scope: data.scope, category: row.category })}`;
      name.append(reviewLink);
      const amount = planHistoryElement("div", "");
      amount.append(planHistoryElement("strong", `${money.format(row.average_cents / 100)} / mês`),
        planHistoryElement("small", `De ${money.format(row.minimum_cents / 100)} a ${money.format(row.maximum_cents / 100)} · presente em ${row.months_present} meses`));
      const track = planHistoryElement("div", "", "bar-track");
      const fill = planHistoryElement("div", "", "bar-fill");
      fill.style.width = `${row.average_cents / max * 100}%`;
      track.append(fill); amount.append(track);
      const label = planHistoryElement("label", "Papel no plano");
      const select = document.createElement("select");
      select.id = `history-category-${index}`;
      select.dataset.category = row.category;
      select.setAttribute("aria-label", `Papel de ${row.category} no plano`);
      for (const [value, text] of [["essential", "Essencial"], ["flexible", "Flexível"], ["review", "A revisar"]]) select.add(new Option(text, value));
      select.value = row.role;
      label.append(select); item.append(name, amount, label); categories.append(item);
    }
    document.querySelector("#history-confirmed-income").value = amountInput(data.preferences.income_cents);
    document.querySelector("#history-buffer").value = data.preferences.buffer_percent;
    document.querySelector("#history-acknowledged").checked = false;
    document.querySelector("#history-use-income").disabled = !data.observed_income_cents;
    planHistoryForm.hidden = !data.categories.length;
    document.querySelector("#history-empty").hidden = Boolean(data.categories.length);
    const recurring = document.querySelector("#history-recurring");
    recurring.replaceChildren(...data.recurring.map(row => {
      const item = planHistoryElement("div", "", "history-evidence-row");
      const title = planHistoryElement("div", row.description);
      title.append(planHistoryElement("small", `${row.category} · ${row.months_present} meses · ${row.note}`));
      item.append(title, planHistoryElement("strong", money.format(row.typical_cents / 100))); return item;
    }));
    if (!data.recurring.length) recurring.append(planHistoryElement("p", "Ainda não há padrões suficientes para sugerir cobranças recorrentes.", "chart-note"));
    const recurringIncome = document.querySelector("#history-recurring-income");
    recurringIncome.replaceChildren(...data.recurring_income.map(row => {
      const item = planHistoryElement("div", "", "history-evidence-row");
      const title = planHistoryElement("div", row.description);
      title.append(planHistoryElement("small", `${money.format(row.typical_cents / 100)} · ${row.months_present} meses · ${row.note}`));
      const button = planHistoryElement("button", "Planejar esta receita", "text-button"); button.type = "button";
      button.addEventListener("click", () => fillExpectedCommitment({ kind: "income", description: row.description.slice(0, 100),
        amount_cents: row.typical_cents, due_day: row.due_day_hint, starts_month: todayLocal().slice(0, 7) }));
      item.append(title, button); return item;
    }));
    if (!data.recurring_income.length) recurringIncome.append(planHistoryElement("p", "Sem padrões suficientes de receita. Cadastre manualmente o que você espera receber.", "chart-note"));
    const future = document.querySelector("#history-future");
    future.replaceChildren(...data.future_invoices.map(row => {
      const item = planHistoryElement("div", "", "history-evidence-row");
      const title = planHistoryElement("div", row.month.split("-").reverse().join("/"));
      title.append(planHistoryElement("small", `${money.format(row.pending_cents / 100)} em compras pendentes`));
      item.append(title, planHistoryElement("strong", money.format(row.amount_cents / 100))); return item;
    }));
    if (!data.future_invoices.length) future.append(planHistoryElement("p", "Nenhuma fatura deste mês ou posterior registrada.", "chart-note"));
    document.querySelector("#plan-history-content").hidden = false;
    planHistoryStatus("Histórico analisado. Revise as sugestões; nenhum valor do plano foi alterado.");
    void previewPlanHistory();
  } catch (error) {
    if (requestId === planHistoryRequestId) planHistoryStatus(error.message, true);
  }
}

async function applyPlanHistory(scenarioId = null) {
  if (!planHistoryCalculation || !document.querySelector("#history-acknowledged").checked) return;
  window.clearTimeout(planHistoryPreviewTimer);
  const requestId = ++planHistoryRequestId;
  const payload = planHistoryPayload();
  planHistoryCalculation = null;
  planHistoryButtons();
  try {
    const result = await request("/plans/history/review", { method: "POST", body: JSON.stringify(payload) });
    if (requestId !== planHistoryRequestId || payload.scope !== reservePlanScope.value) return;
    planHistoryAnalysis = result.analysis;
    renderPlanHistoryCalculation(result);
    reservePlanForm.elements.essential_expenses.value = (result.essential_cents / 100).toFixed(2).replace(".", ",");
    if (scenarioId) {
      const scenario = result.scenarios.find(row => row.id === scenarioId);
      reservePlanForm.elements.monthly_contribution.value = (scenario.contribution_cents / 100).toFixed(2).replace(".", ",");
    }
    reservePlanForm.elements.essential_expenses.dispatchEvent(new Event("input", { bubbles: true }));
    void previewReservePlan();
    planHistoryStatus("Revisão guardada e rascunho preenchido. Confira o plano abaixo e salve quando estiver pronto.", false, true);
  } catch (error) {
    if (requestId === planHistoryRequestId) planHistoryStatus(error.message, true, true);
  }
}

function schedulePlanHistoryPreview() {
  if (!planHistoryAnalysis) return;
  planHistoryRequestId += 1;
  planHistoryCalculation = null;
  planHistoryButtons();
  document.querySelector("#history-scenarios").replaceChildren();
  window.clearTimeout(planHistoryPreviewTimer);
  planHistoryPreviewTimer = window.setTimeout(previewPlanHistory, 400);
}
planHistoryForm.addEventListener("submit", event => event.preventDefault());
planHistoryForm.addEventListener("input", event => {
  if (event.target.id === "history-acknowledged") { planHistoryButtons(); return; }
  // Any change after a confirmation requires another review.
  document.querySelector("#history-acknowledged").checked = false;
  schedulePlanHistoryPreview();
});
document.querySelector("#history-use-income").addEventListener("click", () => {
  if (!planHistoryAnalysis?.observed_income_cents) return;
  document.querySelector("#history-confirmed-income").value = (planHistoryAnalysis.observed_income_cents / 100).toFixed(2).replace(".", ",");
  document.querySelector("#history-confirmed-income").dispatchEvent(new Event("input", { bubbles: true }));
});
document.querySelector("#history-apply-base").addEventListener("click", () => applyPlanHistory());
document.querySelector("#plan-history-refresh").addEventListener("click", refreshPlanHistory);
document.querySelector("#plan-history-window").addEventListener("change", refreshPlanHistory);
reservePlanScope.addEventListener("change", refreshPlanHistory);
reservePlanForm.addEventListener("input", event => {
  if (["reserved_balance", "target_months"].includes(event.target.name)) schedulePlanHistoryPreview();
});
document.addEventListener("plans-view-resume", refreshPlanHistory);
document.addEventListener("reserve-plan-loaded", schedulePlanHistoryPreview);
document.addEventListener("finance-data-change", () => {
  if (!plansView.hidden) planHistoryStatus("Os lançamentos mudaram. Atualize a análise antes de aplicar novas sugestões.");
});
if (!plansView.hidden) void refreshPlanHistory();

const expectedForm = document.querySelector("#expected-form");
let expectedData = null;
let expectedEditId = null;
let expectedRequestId = 0;
document.querySelector("#expected-month").value = todayLocal().slice(0, 7);

function expectedStatus(message, error = false) {
  const node = document.querySelector("#expected-status"); node.textContent = message; node.classList.toggle("error", error);
}
function fillExpectedCommitment(item = null) {
  expectedEditId = item?.id || null;
  expectedForm.reset();
  expectedForm.elements.starts_month.value = todayLocal().slice(0, 7);
  expectedForm.elements.due_day.value = Number(todayLocal().slice(8));
  if (item) {
    for (const key of ["kind", "description", "due_day", "starts_month", "ends_month"]) expectedForm.elements[key].value = item[key] || "";
    expectedForm.elements.amount.value = (item.amount_cents / 100).toFixed(2).replace(".", ",");
    document.querySelector("#expected-editor").open = true;
    document.querySelector("#expected-editor").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  document.querySelector("#expected-save").textContent = expectedEditId ? "Salvar previsão" : "Adicionar previsão";
  document.querySelector("#expected-cancel").hidden = !item;
}
async function refreshExpectedCommitments() {
  if (plansView.hidden) return;
  const requestId = ++expectedRequestId;
  const scope = reservePlanScope.value, month = document.querySelector("#expected-month").value;
  try {
    const data = await request(`/calendar?scope=${scope}&month=${month}`);
    if (requestId !== expectedRequestId || scope !== reservePlanScope.value || month !== document.querySelector("#expected-month").value) return;
    expectedData = data;
    document.querySelector("#expected-income").textContent = money.format(data.expected_income_cents / 100);
    document.querySelector("#expected-expense").textContent = money.format(data.expected_expense_cents / 100);
    document.querySelector("#expected-difference").textContent = money.format((data.expected_income_cents - data.expected_expense_cents) / 100);
    document.querySelector("#expected-use-income").disabled = data.expected_income_cents === 0;
    const list = document.querySelector("#expected-list"); list.replaceChildren();
    for (const item of data.commitments) {
      const row = planHistoryElement("div", "", "expected-row");
      const detail = planHistoryElement("div", "");
      const active = item.starts_month <= month && (!item.ends_month || item.ends_month >= month);
      detail.append(planHistoryElement("strong", `${item.kind === "income" ? "Receita" : "Despesa"} · ${item.description}`),
        planHistoryElement("small", `Dia ${item.due_day} · desde ${item.starts_month}${item.ends_month ? ` até ${item.ends_month}` : " · sem último mês definido"}${active ? "" : " · fora do mês de referência"}`));
      const actions = planHistoryElement("div", "", "row-actions");
      const edit = planHistoryElement("button", "Editar"); edit.type = "button"; edit.addEventListener("click", () => fillExpectedCommitment(item));
      const remove = planHistoryElement("button", "Remover"); remove.type = "button";
      remove.addEventListener("click", async () => {
        if (!window.confirm(`Remover a previsão de ${item.description}? Os lançamentos financeiros continuam registrados.`)) return;
        remove.disabled = true;
        try {
          await request(`/calendar/${item.id}`, { method: "DELETE" });
          if (expectedEditId === item.id) fillExpectedCommitment();
          document.dispatchEvent(new Event("planning-data-change"));
          await refreshExpectedCommitments(); expectedStatus("Previsão removida.");
        } catch (error) { expectedStatus(error.message, true); }
        finally { remove.disabled = false; }
      });
      actions.append(edit, remove); row.append(detail, planHistoryElement("strong", money.format(item.amount_cents / 100)), actions); list.append(row);
    }
    if (!data.commitments.length) list.append(planHistoryElement("p", "Cadastre a renda que espera receber e os pagamentos mensais conhecidos.", "chart-note"));
  } catch (error) { if (requestId === expectedRequestId) expectedStatus(error.message, true); }
}
expectedForm.addEventListener("submit", async event => {
  event.preventDefault(); const fields = expectedForm.elements;
  const scope = reservePlanScope.value, id = expectedEditId;
  const button = document.querySelector("#expected-save"); button.disabled = true;
  try {
    const result = await request(id ? `/calendar/${id}` : "/calendar", { method: id ? "PUT" : "POST", body: JSON.stringify({
      scope, kind: fields.kind.value, description: fields.description.value, amount: fields.amount.value,
      due_day: Number(fields.due_day.value), starts_month: fields.starts_month.value, ends_month: fields.ends_month.value || null }) });
    document.dispatchEvent(new Event("planning-data-change"));
    if (scope !== reservePlanScope.value) return;
    fillExpectedCommitment(); await refreshExpectedCommitments();
    expectedStatus(result.already_registered ? "Esta previsão já estava cadastrada; não foi duplicada." : "Previsão salva na agenda. Ela não cria uma receita ou despesa já realizada.");
  } catch (error) { expectedStatus(error.message, true); }
  finally { button.disabled = false; }
});
document.querySelector("#expected-cancel").addEventListener("click", () => fillExpectedCommitment());
document.querySelector("#expected-month").addEventListener("change", refreshExpectedCommitments);
document.querySelector("#expected-use-income").addEventListener("click", () => {
  if (!expectedData?.expected_income_cents || expectedData.scope !== reservePlanScope.value) return;
  if (planHistoryForm.hidden) { expectedStatus("Preencha seu histórico de despesas antes de comparar os cenários. A previsão já está na agenda."); return; }
  document.querySelector("#history-confirmed-income").value = (expectedData.expected_income_cents / 100).toFixed(2).replace(".", ",");
  document.querySelector("#history-confirmed-income").dispatchEvent(new Event("input", { bubbles: true }));
  expectedStatus("Receita prevista copiada para revisão. Confira se é a renda líquida disponível e confirme a base antes de aplicar ao plano.");
});
reservePlanScope.addEventListener("change", () => { expectedRequestId += 1; expectedData = null; fillExpectedCommitment(); void refreshExpectedCommitments(); });
document.addEventListener("plans-view-resume", refreshExpectedCommitments);
document.addEventListener("finance-data-change", refreshExpectedCommitments);
document.addEventListener("planning-data-change", refreshReservePlan);
fillExpectedCommitment();
if (!plansView.hidden) void refreshExpectedCommitments();
