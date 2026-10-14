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
