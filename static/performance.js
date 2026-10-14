const performanceChart = document.querySelector("#performance-chart");
const performanceSummary = document.querySelector("#performance-summary");
const performanceFlows = document.querySelector("#performance-flows");
const performancePercent = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let performanceRequestId = 0;
const performanceBasis = document.querySelector("#performance-basis");
let selectedPerformanceBasis = "auto";

function renderPortfolioResult(data) {
  const amount = document.querySelector("#portfolio-result-amount");
  const percent = document.querySelector("#portfolio-result-percent");
  const period = document.querySelector("#portfolio-result-period");
  const benchmark = document.querySelector("#portfolio-result-benchmark");
  const note = document.querySelector("#portfolio-result-note");
  document.querySelector("#portfolio-result-source").textContent = data.basis === "pluggy"
    ? "Automático · Pluggy" : data.basis === "total" ? "Saldos completos / manuais" : "Histórico XP";
  period.textContent = data.baseline && data.latest_snapshot
    ? `Histórico de ${formatDate(data.baseline.as_of)} a ${formatDate(data.latest_snapshot.as_of)}`
    : "Histórico de rendimento ainda indisponível";
  const ready = data.status === "ready" && Number.isFinite(data.gain_cents) && Number.isFinite(data.portfolio_percent);
  for (const value of [amount, percent]) {
    value.classList.remove("positive", "negative");
    value.textContent = "—";
  }
  if (!ready) {
    benchmark.textContent = "Aguardando uma comparação válida";
    note.textContent = data.message || "Não foi possível calcular o rendimento agora.";
    return;
  }
  amount.textContent = `${data.gain_cents < 0 ? "−" : data.gain_cents > 0 ? "+" : ""}${money.format(Math.abs(data.gain_cents) / 100)}`;
  percent.textContent = `${data.portfolio_percent < 0 ? "−" : data.portfolio_percent > 0 ? "+" : ""}${performancePercent.format(Math.abs(data.portfolio_percent))}%`;
  amount.classList.add(data.gain_cents < 0 ? "negative" : "positive");
  percent.classList.add(data.portfolio_percent < 0 ? "negative" : "positive");
  benchmark.textContent = Number.isFinite(data.cdi_percent)
    ? `CDI no mesmo período: ${performancePercent.format(data.cdi_percent)}%`
    : "CDI aguardando dados do Banco Central";
  note.textContent = "Estimativa do histórico disponível, com aportes e saques externos ajustados. Proventos recebidos já estão incluídos no resultado.";
}

function performanceAmount(label, cents) {
  const box = document.createElement("div");
  box.className = "performance-metric";
  const caption = document.createElement("span");
  caption.textContent = label;
  const value = document.createElement("strong");
  value.textContent = money.format(cents / 100);
  value.classList.toggle("negative", cents < 0);
  box.append(caption, value);
  return box;
}

function performanceMetric(label, percent, cdi = false) {
  const box = document.createElement("div");
  box.className = "performance-metric" + (cdi ? " cdi" : "");
  const caption = document.createElement("span");
  caption.textContent = label;
  const value = document.createElement("strong");
  value.textContent = percent === null ? "Aguardando dados" : `${percent >= 0 ? "+" : ""}${performancePercent.format(percent)}%`;
  box.append(caption, value);
  return box;
}

function drawPerformance(points) {
  const svg = chartSvg("Rendimento da carteira XP comparado ao CDI");
  const values = points.flatMap(row => [row.portfolio_percent, row.cdi_percent]).filter(Number.isFinite);
  const low = Math.min(0, ...values), high = Math.max(0, ...values);
  const padding = Math.max(1, (high - low) * .12);
  const bottom = low - padding, top = high + padding;
  const first = new Date(points[0].date + "T12:00:00").getTime();
  const last = new Date(points.at(-1).date + "T12:00:00").getTime();
  const x = row => 78 + (new Date(row.date + "T12:00:00").getTime() - first) / Math.max(1, last - first) * 542;
  const y = value => 205 - (value - bottom) / (top - bottom) * 177;
  for (let step = 0; step <= 4; step++) {
    const value = top - (top - bottom) * step / 4;
    const yy = 28 + 177 * step / 4;
    svg.append(svgNode("line", { x1: 78, x2: 620, y1: yy, y2: yy, class: "chart-grid-line" }));
    svg.append(svgNode("text", { x: 70, y: yy + 4, "text-anchor": "end", class: "chart-axis-text" },
      performancePercent.format(value) + "%"));
  }
  for (const series of [
    { key: "portfolio_percent", color: "#5268f5", label: "Carteira XP" },
    { key: "cdi_percent", color: "#00a87b", label: "CDI" },
  ]) {
    const available = points.filter(row => Number.isFinite(row[series.key]));
    if (!available.length) continue;
    const path = available.map((row, index) => `${index ? "L" : "M"}${x(row).toFixed(1)},${y(row[series.key]).toFixed(1)}`).join(" ");
    svg.append(svgNode("path", { d: path, fill: "none", stroke: series.color,
      "stroke-width": 4, "stroke-linecap": "round", class: "chart-line" }));
    available.forEach(row => {
      const message = `${formatDate(row.date)} · ${series.label}: ${performancePercent.format(row[series.key])}%`;
      const dot = svgNode("circle", { cx: x(row), cy: y(row[series.key]), r: 5,
        fill: series.color, stroke: "white", "stroke-width": 2, class: "chart-point" });
      interactiveMark(dot, message, "performance-insight");
      dot.append(svgNode("title", {}, message));
      svg.append(dot);
    });
  }
  [points[0], points.at(-1)].forEach((row, index) => svg.append(svgNode("text", {
    x: x(row), y: 234, "text-anchor": index ? "end" : "start", class: "chart-axis-text"
  }, formatDate(row.date))));
  performanceChart.replaceChildren(svg);
}

function renderPerformance(data) {
  renderPortfolioResult(data);
  performanceBasis.value = data.basis;
  for (const series of data.series) {
    const option = performanceBasis.querySelector(`option[value="${series.id}"]`);
    option.textContent = `${series.label} · ${series.count} datas`;
  }
  const latest = data.latest_snapshot;
  let note = data.valuation_note;
  if (latest && data.basis === "pluggy") {
    note += ` Última posição: ${formatDate(latest.as_of)}.`;
    if (latest.oldest_quote_on && latest.oldest_quote_on < latest.as_of) {
      note += ` Há valores com referência de ${formatDate(latest.oldest_quote_on)}; a estimativa usa as datas informadas pela XP.`;
    }
    if (latest.missing_quote_dates) note += ` ${latest.missing_quote_dates} ativo(s) sem data de cotação.`;
  }
  document.querySelector("#performance-basis-note").textContent = note;
  if (data.benchmark_status === "needs_cdi") {
    document.querySelector("#performance-basis-note").textContent += " Aguardando taxas do Banco Central para comparar com o CDI.";
  }
  performanceSummary.replaceChildren();
  if (data.status !== "ready") {
    if (data.baseline) {
      performanceSummary.append(performanceAmount(`Posição inicial · ${formatDate(data.baseline.as_of)}`, data.baseline.total_cents));
    }
    emptyChart(performanceChart, data.message);
    chartInsight("performance-insight", data.baseline ? `Acompanhamento desde ${formatDate(data.baseline.as_of)}. Atualize a XP nas Configurações para guardar a próxima posição.` : "");
  } else {
    performanceSummary.append(performanceAmount("Ganho no período", data.gain_cents),
      performanceMetric("Carteira XP", data.portfolio_percent),
      performanceMetric("CDI no mesmo período", data.cdi_percent, true),
      performanceAmount("Proventos recebidos · já incluídos no ganho", data.received_income_cents));
    drawPerformance(data.points);
    chartInsight("performance-insight", `${data.snapshot_count} posições XP de ${formatDate(data.points[0].date)} a ${formatDate(data.points.at(-1).date)}. Passe o mouse ou selecione um ponto.`);
  }
  performanceFlows.replaceChildren();
  if (!data.flows.length) return;
  const heading = document.createElement("h3");
  heading.textContent = data.status === "review_flows" ? "Transferências que precisam de revisão" : "Fluxos externos considerados";
  performanceFlows.append(heading);
  for (const flow of data.flows) {
    const row = document.createElement("div");
    row.className = "performance-flow";
    const label = document.createElement("strong");
    label.textContent = `${formatDate(flow.occurred_at.slice(0, 10))} · ${flow.description} · ${flow.amount_cents >= 0 ? "+" : "−"}${money.format(Math.abs(flow.amount_cents) / 100)}`;
    const actions = document.createElement("div");
    actions.className = "performance-flow-actions";
    for (const [value, text] of [["external", "Aporte/saque externo"], ["internal", "Movimento interno"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.classList.toggle("selected", flow.classification === value);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await request(`/investments/flows/${flow.import_key}`, {
            method: "PUT", body: JSON.stringify({ classification: value }),
          });
          await refreshPerformance();
        } catch (error) { chartInsight("performance-insight", error.message); button.disabled = false; }
      });
      actions.append(button);
    }
    row.append(label, actions);
    performanceFlows.append(row);
  }
}

async function refreshPerformance() {
  if (investmentsView.hidden) return;
  const requestId = ++performanceRequestId;
  try {
    const data = await request(`/investments/performance?basis=${selectedPerformanceBasis}`);
    if (requestId !== performanceRequestId) return;
    renderPerformance(data);
  } catch (error) {
    if (requestId !== performanceRequestId) return;
    renderPortfolioResult({ basis: selectedPerformanceBasis, message: "Não foi possível consultar o rendimento agora. Tente novamente ao atualizar a aba." });
    emptyChart(performanceChart, "Não foi possível calcular a comparação.");
    chartInsight("performance-insight", error.message);
  }
}

performanceBasis.addEventListener("change", () => {
  selectedPerformanceBasis = performanceBasis.value;
  refreshPerformance();
});

document.querySelector("#portfolio-result-details").addEventListener("click", () => {
  document.querySelector(".performance-panel").scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    block: "start",
  });
});

document.addEventListener("investments-view-resume", refreshPerformance);
document.addEventListener("investment-data-change", refreshPerformance);
document.addEventListener("finance-view-resume", refreshPerformance);
if (!investmentsView.hidden) refreshPerformance();
