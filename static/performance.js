const performanceChart = document.querySelector("#performance-chart");
const performanceSummary = document.querySelector("#performance-summary");
const performanceFlows = document.querySelector("#performance-flows");
const performancePercent = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let performanceRequestId = 0;

function performanceMetric(label, percent, cdi = false) {
  const box = document.createElement("div");
  box.className = "performance-metric" + (cdi ? " cdi" : "");
  const caption = document.createElement("span");
  caption.textContent = label;
  const value = document.createElement("strong");
  value.textContent = `${percent >= 0 ? "+" : ""}${performancePercent.format(percent)}%`;
  box.append(caption, value);
  return box;
}

function drawPerformance(points) {
  const svg = chartSvg("Rendimento da carteira XP comparado ao CDI");
  const values = points.flatMap(row => [row.portfolio_percent, row.cdi_percent]);
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
    const path = points.map((row, index) => `${index ? "L" : "M"}${x(row).toFixed(1)},${y(row[series.key]).toFixed(1)}`).join(" ");
    svg.append(svgNode("path", { d: path, fill: "none", stroke: series.color,
      "stroke-width": 4, "stroke-linecap": "round", class: "chart-line" }));
    points.forEach(row => {
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
  performanceSummary.replaceChildren();
  if (data.status !== "ready") {
    emptyChart(performanceChart, data.message);
    chartInsight("performance-insight", "");
  } else {
    performanceSummary.append(performanceMetric("Carteira XP", data.portfolio_percent),
      performanceMetric("CDI no mesmo período", data.cdi_percent, true));
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
    const data = await request("/investments/performance");
    if (requestId !== performanceRequestId) return;
    renderPerformance(data);
  } catch (error) {
    if (requestId !== performanceRequestId) return;
    emptyChart(performanceChart, "Não foi possível calcular a comparação.");
    chartInsight("performance-insight", error.message);
  }
}

document.addEventListener("investments-view-resume", refreshPerformance);
document.addEventListener("investment-data-change", refreshPerformance);
document.addEventListener("finance-view-resume", refreshPerformance);
if (!investmentsView.hidden) refreshPerformance();
