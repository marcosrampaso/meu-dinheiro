const SVG_NS = "http://www.w3.org/2000/svg";
const chartColors = { income: "#00a87b", expense: "#ff6b56", invoice: "#5268f5", personal: "#00a87b", business: "#8658ed" };
const categoryColors = {
  "Transporte": "#06a7d8", "Alimentação": "#ff714d", "Compras": "#5a67f2",
  "Outros": "#e75d9b", "Mercado": "#e6a400", "Viagens": "#00a4a6",
  "Assinaturas": "#9b5de5", "Saúde": "#48ae57", "Lazer": "#fa5371",
  "Cuidados pessoais": "#3399ce", "Educação": "#bc7b27", "Moradia": "#b36ad8",
  "Serviços": "#2ba889", "Impostos e taxas": "#ca5780", "Importado": "#9aa8b2",
};
let chartRequestId = 0;
let selectedCategory = null;

function chartInsight(id, message) {
  document.getElementById(id).textContent = message;
}

function interactiveMark(node, message, insightId) {
  node.setAttribute("tabindex", "0");
  node.setAttribute("aria-label", message);
  node.addEventListener("mouseenter", () => chartInsight(insightId, message));
  node.addEventListener("focus", () => chartInsight(insightId, message));
  node.addEventListener("click", () => chartInsight(insightId, message));
}

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
  const svg = svgNode("svg", { viewBox: "0 0 640 260", role: "group", "aria-label": title, preserveAspectRatio: "xMidYMid meet" });
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

function drawBars(target, rows, title, series, insightId) {
  if (!rows.some(row => series.some(item => row[item.key] !== null))) {
    emptyChart(target, "Sem dados neste intervalo.");
    chartInsight(insightId, "Sem dados neste intervalo.");
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
        y: 205 - height, width, height, rx: 5, fill: item.color, class: "chart-bar",
        opacity: row.coverage === "partial" ? .72 : 1,
      });
      const message = `${monthLabel(row.month)}/${row.month.slice(2, 4)} · ${item.label}: ${money.format(value / 100)}${row.coverage === "partial" ? " · extrato parcial" : ""}`;
      interactiveMark(rect, message, insightId);
      rect.append(svgNode("title", {}, message));
      svg.append(rect);
    });
    if (rows.length <= 12 || index % Math.ceil(rows.length / 12) === 0) {
      svg.append(svgNode("text", { x: center, y: 234, "text-anchor": "middle", class: "chart-axis-text" },
        monthLabel(row.month) + (row.month.endsWith("-01") ? "/" + row.month.slice(2, 4) : "") + (row.coverage === "partial" ? "*" : "")));
    }
  });
  target.replaceChildren(svg);
  chartInsight(insightId, "Passe o mouse ou selecione uma barra para ver o valor.");
}

function drawBalances(target, series) {
  const points = series.flatMap(item => item.points);
  if (!points.length) {
    emptyChart(target, "Importe um extrato de conta com coluna de saldo para ver a evolução.");
    chartInsight("balance-insight", "Sem saldos neste intervalo.");
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
    svg.append(svgNode("path", { d: path, fill: "none", stroke: color, "stroke-width": 3.5, "stroke-linejoin": "round", class: "chart-line" }));
    item.points.forEach(point => {
      const marker = svgNode("circle", { cx: x(point.date), cy: y(point.amount_cents), r: 6, fill: color, stroke: "white", "stroke-width": 2, class: "chart-point" });
      const message = `${item.source} · ${formatDate(point.date)} · ${money.format(point.amount_cents / 100)}`;
      interactiveMark(marker, message, "balance-insight");
      marker.append(svgNode("title", {}, message));
      svg.append(marker);
    });
  });
  svg.append(svgNode("text", { x: 78, y: 234, class: "chart-axis-text" }, formatDate(dates[0])));
  svg.append(svgNode("text", { x: 620, y: 234, "text-anchor": "end", class: "chart-axis-text" }, formatDate(dates[dates.length - 1])));
  target.replaceChildren(svg);
  chartInsight("balance-insight", "Passe o mouse ou selecione um ponto para ver o saldo do dia.");
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

function highlightCategory(name, totals) {
  selectedCategory = name;
  for (const segment of document.querySelectorAll(".category-month-segment")) {
    segment.classList.toggle("is-muted", Boolean(name) && segment.dataset.category !== name);
    segment.classList.toggle("is-selected", Boolean(name) && segment.dataset.category === name);
  }
  for (const button of document.querySelectorAll(".category-legend-button")) {
    button.setAttribute("aria-pressed", String(button.dataset.category === name));
    button.classList.toggle("is-selected", button.dataset.category === name);
  }
  chartInsight("category-month-insight", name
    ? `${categoryLabel(name)} · ${money.format(totals.get(name) / 100)} no período. Toque novamente para mostrar todas.`
    : "Toque em uma categoria ou em uma parte da barra para explorar os gastos.");
}

function drawCategorySpending(rows) {
  const fullTotal = rows.reduce((sum, row) => sum + row.total_cents, 0);
  const unknown = rows.reduce((sum, row) => sum + row.categories.filter(item => item.category === "Importado").reduce((subtotal, item) => subtotal + item.amount_cents, 0), 0);
  rows = rows.map(row => {
    const categories = visibleCategories(row.categories);
    return { ...row, original_total_cents: row.total_cents, categories,
             total_cents: categories.reduce((sum, item) => sum + item.amount_cents, 0) };
  });
  document.querySelector("#category-month-coverage").textContent = `${includeUncategorized.checked ? "Todas as compras detalhadas" : "Somente compras categorizadas"} no gráfico. Total detalhado no período: ${money.format(fullTotal / 100)} · ${money.format(unknown / 100)} ainda sem categoria.`;
  const target = document.querySelector("#category-month-chart");
  const legend = document.querySelector("#category-month-legend");
  target.replaceChildren();
  legend.replaceChildren();
  if (!rows.some(row => row.total_cents > 0)) {
    emptyChart(target, fullTotal ? "As compras deste período ainda aguardam categorização." : "Sem compras registradas neste período.");
    selectedCategory = null;
    chartInsight("category-month-insight", fullTotal ? "Há compras detalhadas aguardando categoria; o total continua informado abaixo." : "Sem compras registradas neste período.");
    return;
  }
  const totals = new Map();
  for (const row of rows) {
    for (const item of row.categories) {
      totals.set(item.category, (totals.get(item.category) || 0) + item.amount_cents);
    }
  }
  const names = [...totals.keys()].sort((a, b) => totals.get(b) - totals.get(a) || a.localeCompare(b));
  const colors = new Map(names.map(name => [name, categoryColors[name] || "#5975e2"]));
  const maximum = Math.max(...rows.map(row => row.total_cents));
  for (const row of rows) {
    const line = document.createElement("div");
    line.className = "category-month-row";
    const month = document.createElement("span");
    month.className = "category-month-label";
    month.textContent = `${monthLabel(row.month)}/${row.month.slice(2, 4)}`;
    const track = document.createElement("div");
    track.className = "category-month-track";
    if (row.total_cents) {
      const bar = document.createElement("div");
      bar.className = "category-month-bar";
      bar.style.width = `${100 * row.total_cents / maximum}%`;
      for (const item of row.categories) {
        const segment = document.createElement("button");
        segment.type = "button";
        segment.className = "category-month-segment";
        segment.dataset.category = item.category;
        segment.style.width = `${100 * item.amount_cents / row.total_cents}%`;
        segment.style.backgroundColor = colors.get(item.category);
        const share = Math.round(100 * item.amount_cents / row.total_cents);
        segment.title = `${categoryLabel(item.category)} · ${monthLabel(row.month)}/${row.month.slice(2, 4)} · ${money.format(item.amount_cents / 100)} · ${share}% do valor mostrado`;
        segment.setAttribute("aria-label", segment.title);
        segment.addEventListener("mouseenter", () => chartInsight("category-month-insight", segment.title));
        segment.addEventListener("focus", () => chartInsight("category-month-insight", segment.title));
        segment.addEventListener("mouseleave", () => highlightCategory(selectedCategory, totals));
        segment.addEventListener("click", () => highlightCategory(selectedCategory === item.category ? null : item.category, totals));
        bar.append(segment);
      }
      track.append(bar);
    } else {
      track.textContent = row.original_total_cents ? "Sem compras categorizadas" : "Sem compras registradas";
      track.classList.add("category-month-empty");
    }
    const total = document.createElement("strong");
    total.className = "category-month-total";
    total.textContent = row.total_cents ? money.format(row.total_cents / 100) : "—";
    total.title = `Valor mostrado: ${money.format(row.total_cents / 100)} · Total detalhado do mês: ${money.format(row.original_total_cents / 100)}`;
    line.append(month, track, total);
    if (row.breakdown) {
      const origins = document.createElement("small");
      origins.className = "category-month-origins";
      origins.textContent = [["credit", "Cartão"], ["account", "Conta corrente"]]
        .filter(([origin]) => spendingTypeInput.value === "all" || spendingTypeInput.value === origin)
        .map(([origin, label]) => {
          const values = row.breakdown[origin];
          const amount = values.total_cents - (includeUncategorized.checked ? 0 : values.uncategorized_cents);
          return `${label}: ${money.format(amount / 100)}`;
        }).join(" · ");
      line.append(origins);
    }
    target.append(line);
  }
  for (const name of names) {
    const label = document.createElement("button");
    label.type = "button";
    label.className = "category-legend-button";
    label.dataset.category = name;
    label.setAttribute("aria-pressed", String(selectedCategory === name));
    label.setAttribute("aria-label", `${categoryLabel(name)}: ${money.format(totals.get(name) / 100)} no período`);
    const swatch = document.createElement("i");
    swatch.style.backgroundColor = colors.get(name);
    label.append(swatch, document.createTextNode(categoryLabel(name)));
    label.addEventListener("click", () => highlightCategory(selectedCategory === name ? null : name, totals));
    label.addEventListener("mouseenter", () => chartInsight("category-month-insight", `${categoryLabel(name)} · ${money.format(totals.get(name) / 100)} no período`));
    label.addEventListener("focus", () => chartInsight("category-month-insight", `${categoryLabel(name)} · ${money.format(totals.get(name) / 100)} no período`));
    label.addEventListener("mouseleave", () => highlightCategory(selectedCategory, totals));
    legend.append(label);
  }
  highlightCategory(totals.has(selectedCategory) ? selectedCategory : null, totals);
}

async function refreshCharts() {
  const requestId = ++chartRequestId;
  const selectedMonth = monthInput.value;
  const selectedScope = scopeInput.value;
  const selectedMonths = document.querySelector("#chart-months").value;
  const selectedPaymentType = spendingTypeInput.value;
  try {
    const data = await request(`/analytics?month=${encodeURIComponent(selectedMonth)}&scope=${encodeURIComponent(selectedScope)}&months=${selectedMonths}&payment_type=${selectedPaymentType}`);
    if (requestId !== chartRequestId || selectedMonth !== monthInput.value || selectedScope !== scopeInput.value || selectedMonths !== document.querySelector("#chart-months").value || selectedPaymentType !== spendingTypeInput.value) return;
    drawBars(document.querySelector("#cashflow-chart"), data.cashflow, "Entradas e saídas por mês", [
      { key: "income_cents", label: "Entradas", color: chartColors.income },
      { key: "expense_cents", label: "Saídas", color: chartColors.expense },
    ], "cashflow-insight");
    const partial = data.cashflow.filter(row => row.coverage === "partial")
      .map(row => `${row.month.slice(5)}/${row.month.slice(0, 4)}`);
    document.querySelector("#cashflow-note").textContent = partial.length
      ? `* Extrato parcial em ${partial.join(", ")}. Meses sem extrato não aparecem como zero.`
      : "Meses sem extrato não aparecem como zero.";
    drawBars(document.querySelector("#invoice-chart"), data.invoices, "Faturas por mês", [
      { key: "total_cents", label: "Fatura", color: chartColors.invoice },
    ], "invoice-insight");
    document.querySelector("#category-month-chart-title").textContent = `Gastos mensais por categoria · ${spendingTypeInput.selectedOptions[0].textContent}`;
    drawCategorySpending(data.category_spending);
    document.querySelector("#invoice-chart-note").textContent = "O mês corresponde à fatura; as compras não são somadas novamente às saídas da conta.";
    drawBalances(document.querySelector("#balance-chart"), data.balance_series);
    renderChartLegend(data.balance_series);
    document.querySelector("#balance-chart-note").textContent = "Cada ponto é o saldo de um dia com lançamentos no extrato. As contas Pessoal e PJ ficam em séries separadas.";
  } catch (error) {
    if (requestId !== chartRequestId) return;
    for (const id of ["cashflow-chart", "invoice-chart", "category-month-chart", "balance-chart"]) {
      emptyChart(document.getElementById(id), "Não foi possível carregar o gráfico.");
    }
    for (const id of ["cashflow-insight", "invoice-insight", "category-month-insight", "balance-insight"]) chartInsight(id, "Dados indisponíveis.");
    document.querySelector("#category-month-legend").replaceChildren();
    showNotice(error.message, true);
  }
}

document.addEventListener("finance-data-change", refreshCharts);
document.addEventListener("finance-view-resume", refreshCharts);
document.querySelector("#chart-months").addEventListener("change", refreshCharts);
refreshCharts();
