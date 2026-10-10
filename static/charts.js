const SVG_NS = "http://www.w3.org/2000/svg";
const chartColors = { income: "#2d9568", expense: "#e29564", invoice: "#5a7dc3", personal: "#2d9568", business: "#735bb8" };
let chartRequestId = 0;

function svgNode(tag, attributes = {}, label = "") {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (label) node.textContent = label;
  return node;
}

function axisLabel(cents) {
  const reais = cents / 100;
  return "R$ " + new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 }).format(reais);
}

function chartSvg(title) {
  const svg = svgNode("svg", { viewBox: "0 0 640 260", role: "img", "aria-label": title, preserveAspectRatio: "xMidYMid meet" });
  svg.append(svgNode("title", {}, title));
  return svg;
}

function drawFrame(svg, topValue, bottomValue = 0) {
  const top = 28, bottom = 205;
  for (let step = 0; step <= 4; step++) {
    const y = top + (bottom - top) * step / 4;
    const value = topValue + (bottomValue - topValue) * step / 4;
    svg.append(svgNode("line", { x1: 78, x2: 620, y1: y, y2: y, class: "chart-grid-line" }));
    svg.append(svgNode("text", { x: 70, y: y + 4, "text-anchor": "end", class: "chart-axis-text" }, axisLabel(value)));
  }
}

function monthLabel(month) {
  return new Intl.DateTimeFormat("pt-BR", { month: "short" }).format(new Date(month + "-01T12:00:00")).replace(".", "");
}

function emptyChart(target, message) {
  target.replaceChildren();
  const paragraph = document.createElement("p");
  paragraph.className = "chart-empty";
  paragraph.textContent = message;
  target.append(paragraph);
}

function drawBars(target, rows, title, series) {
  if (!rows.some(row => series.some(item => row[item.key] !== null))) {
    emptyChart(target, "Sem dados neste intervalo.");
    return;
  }
  const svg = chartSvg(title);
  const maxValue = Math.max(1, ...rows.flatMap(row => series.map(item => Math.max(0, row[item.key] ?? 0))));
  const topValue = maxValue * 1.15;
  drawFrame(svg, topValue);
  const groupWidth = 542 / rows.length;
  rows.forEach((row, index) => {
    const center = 78 + groupWidth * (index + .5);
    const width = Math.min(25, groupWidth / (series.length + 1));
    series.forEach((item, itemIndex) => {
      const value = row[item.key];
      if (value === null) return;
      const height = Math.max(1.5, value / topValue * 177);
      const rect = svgNode("rect", {
        x: center + (itemIndex - (series.length - 1) / 2) * (width + 4) - width / 2,
        y: 205 - height, width, height, rx: 3, fill: item.color,
        opacity: row.coverage === "partial" ? .72 : 1,
      });
      rect.append(svgNode("title", {}, `${row.month}: ${item.label} ${money.format(value / 100)}${row.coverage === "partial" ? " (extrato parcial)" : ""}`));
      svg.append(rect);
    });
    if (rows.length <= 12 || index % Math.ceil(rows.length / 12) === 0) {
      svg.append(svgNode("text", { x: center, y: 234, "text-anchor": "middle", class: "chart-axis-text" },
        monthLabel(row.month) + (row.month.endsWith("-01") ? "/" + row.month.slice(2, 4) : "") + (row.coverage === "partial" ? "*" : "")));
    }
  });
  target.replaceChildren(svg);
}

function drawBalances(target, series) {
  const points = series.flatMap(item => item.points);
  if (!points.length) {
    emptyChart(target, "Importe um extrato de conta com coluna de saldo para ver a evolução.");
    return;
  }
  const dates = points.map(point => point.date).sort();
  const first = new Date(dates[0] + "T12:00:00").getTime();
  const last = new Date(dates[dates.length - 1] + "T12:00:00").getTime();
  const values = points.map(point => point.amount_cents);
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(0, ...values);
  const range = Math.max(100, maxValue - minValue);
  const lower = minValue - range * .08;
  const upper = maxValue + range * .08;
  const svg = chartSvg("Evolução do saldo bancário por conta");
  drawFrame(svg, upper, lower);
  const x = date => 78 + (last === first ? .5 : (new Date(date + "T12:00:00").getTime() - first) / (last - first)) * 542;
  const y = cents => 205 - (cents - lower) / (upper - lower) * 177;
  series.forEach(item => {
    const color = chartColors[item.scope];
    const path = item.points.map((point, index) => `${index ? "L" : "M"}${x(point.date).toFixed(1)},${y(point.amount_cents).toFixed(1)}`).join(" ");
    svg.append(svgNode("path", { d: path, fill: "none", stroke: color, "stroke-width": 3, "stroke-linejoin": "round" }));
    item.points.forEach(point => {
      const marker = svgNode("circle", { cx: x(point.date), cy: y(point.amount_cents), r: 4, fill: color, stroke: "white", "stroke-width": 1.5 });
      marker.append(svgNode("title", {}, `${item.source} · ${formatDate(point.date)} · ${money.format(point.amount_cents / 100)}`));
      svg.append(marker);
    });
  });
  svg.append(svgNode("text", { x: 78, y: 234, class: "chart-axis-text" }, formatDate(dates[0])));
  svg.append(svgNode("text", { x: 620, y: 234, "text-anchor": "end", class: "chart-axis-text" }, formatDate(dates[dates.length - 1])));
  target.replaceChildren(svg);
}

function renderChartLegend(series) {
  const legend = document.querySelector("#balance-chart-legend");
  legend.replaceChildren();
  for (const item of series) {
    const label = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.backgroundColor = chartColors[item.scope];
    label.append(swatch, document.createTextNode(item.source + (item.scope === "business" ? " · PJ" : " · Pessoal")));
    legend.append(label);
  }
}

async function refreshCharts() {
  const requestId = ++chartRequestId;
  const selectedMonth = monthInput.value;
  const selectedScope = scopeInput.value;
  const selectedMonths = document.querySelector("#chart-months").value;
  try {
    const data = await request(`/analytics?month=${encodeURIComponent(selectedMonth)}&scope=${encodeURIComponent(selectedScope)}&months=${selectedMonths}`);
    if (requestId !== chartRequestId || selectedMonth !== monthInput.value || selectedScope !== scopeInput.value || selectedMonths !== document.querySelector("#chart-months").value) return;
    drawBars(document.querySelector("#cashflow-chart"), data.cashflow, "Entradas e saídas por mês", [
      { key: "income_cents", label: "Entradas", color: chartColors.income },
      { key: "expense_cents", label: "Saídas", color: chartColors.expense },
    ]);
    const partial = data.cashflow.filter(row => row.coverage === "partial")
      .map(row => `${row.month.slice(5)}/${row.month.slice(0, 4)}`);
    document.querySelector("#cashflow-note").textContent = partial.length
      ? `* Extrato parcial em ${partial.join(", ")}. Meses sem extrato não aparecem como zero.`
      : "Meses sem extrato não aparecem como zero.";
    drawBars(document.querySelector("#invoice-chart"), data.invoices, "Faturas por mês", [
      { key: "total_cents", label: "Fatura", color: chartColors.invoice },
    ]);
    document.querySelector("#invoice-chart-note").textContent = "O mês corresponde à fatura; as compras não são somadas novamente às saídas da conta.";
    drawBalances(document.querySelector("#balance-chart"), data.balance_series);
    renderChartLegend(data.balance_series);
    document.querySelector("#balance-chart-note").textContent = "Cada ponto é o saldo de um dia com lançamentos no extrato. As contas Pessoal e PJ ficam em séries separadas.";
  } catch (error) {
    if (requestId !== chartRequestId) return;
    for (const id of ["cashflow-chart", "invoice-chart", "balance-chart"]) {
      emptyChart(document.getElementById(id), "Não foi possível carregar o gráfico.");
    }
    showNotice(error.message, true);
  }
}

document.addEventListener("finance-data-change", refreshCharts);
document.querySelector("#chart-months").addEventListener("change", refreshCharts);
refreshCharts();
