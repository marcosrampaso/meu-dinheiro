const investmentMonth = document.querySelector("#investment-month");
const investmentMonths = document.querySelector("#investment-months");
const investmentStatus = document.querySelector("#investment-status");
const financesTab = document.querySelector("#finances-tab");
const investmentsTab = document.querySelector("#investments-tab");
const financesView = document.querySelector("#finances-view");
const investmentsView = document.querySelector("#investments-view");
let investmentRequestId = 0;

function setArea(area) {
  const showInvestments = area === "investments";
  financesView.hidden = showInvestments;
  investmentsView.hidden = !showInvestments;
  financesTab.setAttribute("aria-selected", String(!showInvestments));
  investmentsTab.setAttribute("aria-selected", String(showInvestments));
  financesTab.tabIndex = showInvestments ? -1 : 0;
  investmentsTab.tabIndex = showInvestments ? 0 : -1;
  if (showInvestments) refreshInvestments();
}

function syncAreaFromHash() {
  setArea(window.location.hash === "#investimentos" ? "investments" : "finances");
}

financesTab.addEventListener("click", () => { window.location.hash = "financas"; syncAreaFromHash(); });
investmentsTab.addEventListener("click", () => { window.location.hash = "investimentos"; syncAreaFromHash(); });
window.addEventListener("hashchange", syncAreaFromHash);

function drawInvestmentCumulative(rows) {
  const target = document.querySelector("#investment-cumulative-chart");
  const points = rows.filter(row => row.cumulative_cents !== null);
  if (!points.length) {
    emptyChart(target, "Ainda não há aportes registrados neste intervalo.");
    chartInsight("investment-cumulative-insight", "Sem aportes neste intervalo.");
    return;
  }
  const svg = chartSvg("Aportes acumulados na XP");
  const upper = Math.max(100, ...points.map(row => row.cumulative_cents)) * 1.15;
  drawFrame(svg, upper);
  const x = index => 78 + (rows.length === 1 ? .5 : index / (rows.length - 1)) * 542;
  const y = cents => 205 - cents / upper * 177;
  const path = points.map((row, index) => {
    const monthIndex = rows.findIndex(item => item.month === row.month);
    return `${index ? "L" : "M"}${x(monthIndex).toFixed(1)},${y(row.cumulative_cents).toFixed(1)}`;
  }).join(" ");
  svg.append(svgNode("path", { d: path, fill: "none", stroke: "#8658ed",
    "stroke-width": 4, "stroke-linecap": "round", "stroke-linejoin": "round", class: "chart-line" }));
  points.forEach(row => {
    const index = rows.findIndex(item => item.month === row.month);
    const message = `${monthLabel(row.month)}/${row.month.slice(2, 4)} · Aportes acumulados: ${money.format(row.cumulative_cents / 100)}`;
    const marker = svgNode("circle", { cx: x(index), cy: y(row.cumulative_cents), r: 6,
      fill: "#8658ed", stroke: "white", "stroke-width": 2, class: "chart-point" });
    interactiveMark(marker, message, "investment-cumulative-insight");
    marker.append(svgNode("title", {}, message));
    svg.append(marker);
  });
  rows.forEach((row, index) => {
    if (rows.length <= 12 || index % Math.ceil(rows.length / 12) === 0) {
      svg.append(svgNode("text", { x: x(index), y: 234, "text-anchor": "middle", class: "chart-axis-text" },
        monthLabel(row.month) + (row.month.endsWith("-01") ? "/" + row.month.slice(2, 4) : "")));
    }
  });
  target.replaceChildren(svg);
  chartInsight("investment-cumulative-insight", "Passe o mouse ou selecione um ponto para ver o valor.");
}

function renderInvestmentMovements(data) {
  const target = document.querySelector("#investment-movements");
  target.replaceChildren();
  document.querySelector("#investment-count").textContent =
    `${data.movement_count} ${data.movement_count === 1 ? "movimentação" : "movimentações"} no período`;
  if (!data.movements.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhuma movimentação encontrada neste período. Importe um CSV da XP abaixo.";
    target.append(empty);
    return;
  }
  const labels = { deposit: "Aporte", allocation: "Aplicação", other: "Outra movimentação" };
  for (const item of data.movements) {
    const row = document.createElement("div");
    row.className = "investment-row";
    const details = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = item.description;
    const meta = document.createElement("small");
    meta.textContent = `${labels[item.type]} · ${formatDate(item.occurred_at.slice(0, 10))} ${item.occurred_at.slice(11, 16)}${item.matched_inter ? " · Conciliado com Inter" : ""}`;
    details.append(name, meta);
    const amount = document.createElement("b");
    amount.className = item.amount_cents > 0 ? "investment-in" : "investment-out";
    amount.textContent = `${item.amount_cents > 0 ? "+" : "−"} ${money.format(Math.abs(item.amount_cents) / 100)}`;
    row.append(details, amount);
    target.append(row);
  }
}

async function refreshInvestments() {
  if (investmentsView.hidden) return;
  const requestId = ++investmentRequestId;
  const month = investmentMonth.value;
  const months = investmentMonths.value;
  try {
    const data = await request(`/investments/overview?month=${encodeURIComponent(month)}&months=${months}`);
    if (requestId !== investmentRequestId || month !== investmentMonth.value || months !== investmentMonths.value) return;
    document.querySelector("#investment-total").textContent = money.format(data.total_deposited_cents / 100);
    document.querySelector("#investment-period-total").textContent = money.format(data.period_deposited_cents / 100);
    document.querySelector("#investment-matched").textContent = `${data.matched_count} PIX`;
    document.querySelector("#investment-matched-amount").textContent = `${money.format(data.matched_cents / 100)} no período`;
    drawBars(document.querySelector("#investment-deposits-chart"), data.months,
      "Aportes por mês na XP", [{ key: "deposits_cents", label: "Aportes", color: "#00a87b" }],
      "investment-deposits-insight");
    drawInvestmentCumulative(data.months);
    renderInvestmentMovements(data);
    investmentStatus.textContent = "";
    investmentStatus.classList.remove("error");
  } catch (error) {
    if (requestId !== investmentRequestId) return;
    investmentStatus.textContent = error.message;
    investmentStatus.classList.add("error");
  }
}

investmentMonth.value = todayLocal().slice(0, 7);
investmentMonth.addEventListener("change", refreshInvestments);
investmentMonths.addEventListener("change", refreshInvestments);
document.addEventListener("investment-data-change", refreshInvestments);
document.addEventListener("finance-view-resume", refreshInvestments);
syncAreaFromHash();
