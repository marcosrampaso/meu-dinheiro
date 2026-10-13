const investmentMonth = document.querySelector("#investment-month");
const investmentMonths = document.querySelector("#investment-months");
const investmentStatus = document.querySelector("#investment-status");
const financesTab = document.querySelector("#finances-tab");
const investmentsTab = document.querySelector("#investments-tab");
const newsTab = document.querySelector("#news-tab");
const financesView = document.querySelector("#finances-view");
const investmentsView = document.querySelector("#investments-view");
const newsView = document.querySelector("#news-view");
let investmentRequestId = 0;
let cdiRequestId = 0;
let cdiInitialRetries = 0;
const cdiPercent = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 4 });

function drawCDI(data) {
  const target = document.querySelector("#cdi-chart");
  const total = document.querySelector("#cdi-total");
  const note = document.querySelector("#cdi-note");
  const source = '<a href="https://www3.bcb.gov.br/sgspub/consultarvalores/consultarValoresSeries.do?method=consultarSeries&amp;series=12" target="_blank" rel="noopener noreferrer">Banco Central, série 12</a>';
  if (!data.points.length) {
    total.textContent = "—";
    emptyChart(target, data.error ? "Não foi possível consultar o CDI." : "Buscando taxas do CDI...");
    chartInsight("cdi-insight", data.error || "Aguardando dados publicados pelo Banco Central.");
    note.innerHTML = `Fonte: ${source}. Acumulado composto por dia útil, antes de impostos e taxas.`;
    return;
  }
  total.textContent = `+${cdiPercent.format(data.cumulative_percent)}%`;
  const points = data.points;
  const upper = Math.max(0.1, data.cumulative_percent * 1.16);
  const first = new Date(data.starts_on + "T12:00:00").getTime();
  const last = new Date(data.latest_on + "T12:00:00").getTime();
  const x = row => 78 + ((new Date(row.date + "T12:00:00").getTime() - first) / Math.max(1, last - first)) * 542;
  const y = row => 205 - row.cumulative_percent / upper * 177;
  const svg = chartSvg("CDI acumulado de " + formatDate(data.starts_on) + " a " + formatDate(data.latest_on));
  for (let step = 0; step <= 4; step++) {
    const value = upper * (4 - step) / 4;
    const position = 28 + 177 * step / 4;
    svg.append(svgNode("line", { x1: 78, x2: 620, y1: position, y2: position, class: "chart-grid-line" }));
    svg.append(svgNode("text", { x: 70, y: position + 4, "text-anchor": "end", class: "chart-axis-text" },
      cdiPercent.format(value) + "%"));
  }
  svg.append(svgNode("path", {
    d: points.map((row, index) => `${index ? "L" : "M"}${x(row).toFixed(1)},${y(row).toFixed(1)}`).join(" "),
    fill: "none", stroke: "#5a67f2", "stroke-width": 4,
    "stroke-linecap": "round", "stroke-linejoin": "round", class: "chart-line"
  }));
  const stride = Math.max(1, Math.ceil((points.length - 1) / 18));
  points.forEach((row, index) => {
    if (index !== 0 && index !== points.length - 1 && index % stride !== 0) return;
    const message = `${formatDate(row.date)} · CDI acumulado: ${cdiPercent.format(row.cumulative_percent)}%`;
    const marker = svgNode("circle", { cx: x(row), cy: y(row), r: index === points.length - 1 ? 6 : 4,
      fill: "#5a67f2", stroke: "white", "stroke-width": 2, class: "chart-point" });
    interactiveMark(marker, message, "cdi-insight");
    marker.append(svgNode("title", {}, message));
    svg.append(marker);
  });
  const labels = [points[0], points[Math.floor((points.length - 1) / 2)], points[points.length - 1]];
  labels.forEach((row, index) => svg.append(svgNode("text", { x: x(row), y: 234,
    "text-anchor": ["start", "middle", "end"][index], class: "chart-axis-text" }, formatDate(row.date))));
  target.replaceChildren(svg);
  chartInsight("cdi-insight", `CDI acumulado: ${cdiPercent.format(data.cumulative_percent)}% de ${formatDate(data.starts_on)} até ${formatDate(data.latest_on)}.`);
  note.innerHTML = `Fonte: ${source}. Acumulado composto por dia útil, antes de impostos e taxas. Último dado: ${formatDate(data.latest_on)}.${data.stale ? " Dados publicados há mais de 7 dias." : ""}${data.error ? " Atualização indisponível; exibindo dados salvos." : ""}`;
}

async function refreshCDI() {
  if (investmentsView.hidden) return;
  const requestId = ++cdiRequestId;
  const months = document.querySelector("#cdi-months").value;
  try {
    const data = await request(`/investments/cdi?months=${months}`);
    if (requestId !== cdiRequestId || months !== document.querySelector("#cdi-months").value) return;
    drawCDI(data);
    if (!data.points.length && !data.checked_at && !data.error && cdiInitialRetries++ < 3) {
      window.setTimeout(refreshCDI, 5000);
    }
  } catch (error) {
    if (requestId !== cdiRequestId) return;
    emptyChart(document.querySelector("#cdi-chart"), "Não foi possível carregar o CDI.");
    chartInsight("cdi-insight", error.message);
  }
}

function setArea(area) {
  const showInvestments = area === "investments";
  const showNews = area === "news";
  financesView.hidden = showInvestments || showNews;
  investmentsView.hidden = !showInvestments;
  newsView.hidden = !showNews;
  financesTab.setAttribute("aria-selected", String(!showInvestments && !showNews));
  investmentsTab.setAttribute("aria-selected", String(showInvestments));
  newsTab.setAttribute("aria-selected", String(showNews));
  financesTab.tabIndex = showInvestments || showNews ? -1 : 0;
  investmentsTab.tabIndex = showInvestments ? 0 : -1;
  newsTab.tabIndex = showNews ? 0 : -1;
  if (showInvestments) { refreshInvestments(); refreshCDI(); document.dispatchEvent(new Event("investments-view-resume")); }
  if (showNews) document.dispatchEvent(new Event("news-view-resume"));
}

function syncAreaFromHash() {
  setArea(window.location.hash === "#investimentos" ? "investments" :
    window.location.hash === "#noticias" ? "news" : "finances");
}

financesTab.addEventListener("click", () => { window.location.hash = "financas"; syncAreaFromHash(); });
investmentsTab.addEventListener("click", () => { window.location.hash = "investimentos"; syncAreaFromHash(); });
newsTab.addEventListener("click", () => { window.location.hash = "noticias"; syncAreaFromHash(); });
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

function renderInvestmentPosition(position) {
  const allocation = document.querySelector("#investment-allocation");
  const assets = document.querySelector("#investment-assets");
  allocation.replaceChildren();
  assets.replaceChildren();
  if (!position) {
    document.querySelector("#investment-portfolio-total").textContent = "—";
    document.querySelector("#investment-position-date").textContent = "Importe a posição detalhada";
    document.querySelector("#investment-portfolio-count").textContent = "—";
    document.querySelector("#investment-portfolio-note").textContent = "Não há posição importada até este mês.";
    renderInvestmentReturns(null);
    return;
  }
  document.querySelector("#investment-portfolio-total").textContent = money.format(position.total_cents / 100);
  document.querySelector("#investment-position-date").textContent = `Posição de ${formatDate(position.as_of)}`;
  document.querySelector("#investment-portfolio-count").textContent = `${position.assets.length} ativos`;
  const cost = position.assets.reduce((sum, asset) => sum + asset.cost_cents, 0);
  const difference = position.assets_cents - cost;
  document.querySelector("#investment-portfolio-note").textContent =
    `Ativos: ${money.format(position.assets_cents / 100)} · Proventos a receber: ${money.format(position.receivables_cents / 100)} · Diferença entre valor bruto e custo dos ativos: ${difference < 0 ? "−" : "+"}${money.format(Math.abs(difference) / 100)}. Não equivale ao rendimento total da carteira.`;
  const colors = { "Ações": "#8658ed", "Fundos de Investimentos": "#00a87b", "Fundos Imobiliários": "#ef8a37" };
  const byCategory = new Map();
  for (const asset of position.assets) byCategory.set(asset.category, (byCategory.get(asset.category) || 0) + asset.value_cents);
  for (const [category, value] of byCategory) {
    const row = document.createElement("div");
    row.className = "allocation-row";
    const label = document.createElement("div");
    label.className = "category-label";
    const name = document.createElement("span");
    name.textContent = category;
    const amount = document.createElement("span");
    amount.textContent = `${money.format(value / 100)} · ${(value / position.assets_cents * 100).toFixed(1).replace(".", ",")}%`;
    label.append(name, amount);
    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = "bar-fill";
    fill.style.width = `${value / position.assets_cents * 100}%`;
    fill.style.background = colors[category] || "#268cb3";
    track.append(fill);
    row.append(label, track);
    allocation.append(row);
  }
  for (const asset of position.assets) {
    const row = document.createElement("div");
    row.className = "investment-row";
    const details = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = asset.name;
    const meta = document.createElement("small");
    meta.textContent = `${asset.category}${asset.quantity ? ` · ${asset.quantity} cotas` : ""} · Custo ${money.format(asset.cost_cents / 100)}`;
    details.append(name, meta);
    const amount = document.createElement("b");
    amount.textContent = money.format(asset.value_cents / 100);
    row.append(details, amount);
    assets.append(row);
  }
  renderInvestmentReturns(position);
}

function renderInvestmentReturns(position) {
  const target = document.querySelector("#investment-return-chart");
  target.replaceChildren();
  const total = document.querySelector("#investment-return-total");
  const note = document.querySelector("#investment-return-note");
  if (!position || !position.assets.length) {
    total.textContent = "—";
    note.textContent = "Importe uma posição detalhada para comparar custo e valor atual.";
    return;
  }
  const cost = position.assets.reduce((sum, asset) => sum + asset.cost_cents, 0);
  const change = position.assets_cents - cost;
  total.textContent = `${change < 0 ? "−" : "+"}${money.format(Math.abs(change) / 100)}`;
  total.classList.toggle("negative", change < 0);
  note.textContent = `Variação bruta estimada: ${cost ? (change / cost * 100).toFixed(2).replace(".", ",") : "0,00"}% sobre o custo dos ativos em ${formatDate(position.as_of)}. Proventos a receber e impostos não entram neste gráfico. Não representa a rentabilidade histórica total.`;
  const maxAbs = Math.max(1, ...position.assets.map(asset => Math.abs(asset.value_cents - asset.cost_cents)));
  for (const asset of position.assets) {
    const delta = asset.value_cents - asset.cost_cents;
    const percent = asset.cost_cents ? delta / asset.cost_cents * 100 : 0;
    const row = document.createElement("div");
    row.className = "return-row";
    const name = document.createElement("strong");
    name.textContent = asset.name;
    const track = document.createElement("div");
    track.className = "return-track";
    const zero = document.createElement("span");
    zero.className = "return-zero";
    const bar = document.createElement("span");
    bar.className = `return-bar ${delta < 0 ? "loss" : "gain"}`;
    bar.style.width = `${Math.max(delta === 0 ? 0 : 1, Math.abs(delta) / maxAbs * 45)}%`;
    bar.style.left = delta < 0 ? `${50 - Math.abs(delta) / maxAbs * 45}%` : "50%";
    track.append(zero, bar);
    const value = document.createElement("span");
    value.className = delta < 0 ? "return-loss" : "return-gain";
    value.textContent = `${delta < 0 ? "−" : "+"}${money.format(Math.abs(delta) / 100)} (${percent < 0 ? "−" : "+"}${Math.abs(percent).toFixed(2).replace(".", ",")}%)`;
    row.append(name, track, value);
    target.append(row);
  }
}

function renderMarketReturns(data) {
  const target = document.querySelector("#market-return-chart");
  const total = document.querySelector("#market-return-total");
  const note = document.querySelector("#market-return-note");
  target.replaceChildren();
  document.querySelector("#brapi-token-state").textContent = data.brapi_token_configured ? "Token salvo neste computador" : "Token ainda não informado";
  document.querySelector("#brapi-token-delete").hidden = !data.brapi_token_configured;
  if (!data.as_of) {
    total.textContent = "—";
    note.textContent = "Importe uma posição detalhada da XP para acompanhar os ativos.";
    return;
  }
  const displayedGain = asset => asset.gain_cents === null
    ? asset.snapshot_value_cents - asset.cost_cents : asset.gain_cents;
  const maxAbs = Math.max(1, ...data.assets.map(asset => Math.abs(displayedGain(asset))));
  if (data.complete) {
    const gain = data.estimated_gain_cents;
    total.textContent = `${gain < 0 ? "−" : "+"}${money.format(Math.abs(gain) / 100)}`;
    total.classList.toggle("negative", gain < 0);
  } else {
    total.textContent = "Parcial";
    total.classList.remove("negative");
  }
  note.textContent = `Posição base de ${formatDate(data.as_of)}. Barras sem cotação nova mostram o valor da posição XP. Consulta automática a cada 3 horas enquanto o servidor estiver aberto${data.checked_at ? ` · última tentativa ${new Date(data.checked_at).toLocaleString("pt-BR")}` : " · primeira consulta em andamento"}. Valores estimados pressupõem posições sem novas compras ou vendas; proventos, taxas e impostos não entram.`;
  for (const asset of data.assets) {
    const row = document.createElement("div");
    row.className = `market-return-row${asset.gain_cents === null ? " baseline" : ""}`;
    const heading = document.createElement("div");
    heading.className = "market-return-heading";
    const name = document.createElement("strong");
    name.textContent = asset.name;
    const value = document.createElement("b");
    {
      const gain = displayedGain(asset);
      const percent = asset.cost_cents ? gain / asset.cost_cents * 100 : 0;
      value.textContent = `${gain < 0 ? "−" : "+"}${money.format(Math.abs(gain) / 100)} (${percent < 0 ? "−" : "+"}${Math.abs(percent).toFixed(2).replace(".", ",")}%)${asset.gain_cents === null ? " · XP" : ""}`;
      value.className = gain < 0 ? "return-loss" : "return-gain";
    }
    heading.append(name, value);
    row.append(heading);
    {
      const gain = displayedGain(asset);
      const track = document.createElement("div");
      track.className = "return-track";
      const zero = document.createElement("span");
      zero.className = "return-zero";
      const bar = document.createElement("span");
      bar.className = `return-bar ${gain < 0 ? "loss" : "gain"}`;
      bar.style.width = `${Math.max(gain === 0 ? 0 : 1, Math.abs(gain) / maxAbs * 45)}%`;
      bar.style.left = gain < 0 ? `${50 - Math.abs(gain) / maxAbs * 45}%` : "50%";
      track.append(zero, bar);
      row.append(track);
    }
    const detail = document.createElement("small");
    detail.textContent = `${asset.quote_date ? `Cotação ${formatDate(asset.quote_date)} · valor estimado ${money.format(asset.estimated_value_cents / 100)} · ` : `Posição XP ${formatDate(data.as_of)} · aguardando nova cotação · `}${asset.status}`;
    if (asset.source_url) {
      const link = document.createElement("a");
      link.href = asset.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = asset.source === "cvm" ? "Fonte: CVM" : "Fonte: brapi";
      detail.append(" · ", link);
    }
    row.append(detail);
    target.append(row);
  }
}

async function refreshMarketReturns() {
  if (investmentsView.hidden) return;
  try {
    renderMarketReturns(await request("/investments/market-returns"));
  } catch (error) {
    document.querySelector("#market-return-note").textContent = error.message;
  }
}

document.querySelector("#brapi-token-form").addEventListener("submit", async event => {
  event.preventDefault();
  const input = document.querySelector("#brapi-token-input");
  const status = document.querySelector("#brapi-token-status");
  try {
    await request("/investments/brapi-token", { method: "POST", body: JSON.stringify({ token: input.value.trim() }) });
    input.value = "";
    status.textContent = "Token salvo. A consulta das cotações começou.";
    status.classList.remove("error");
    await refreshMarketReturns();
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  }
});

document.querySelector("#brapi-token-delete").addEventListener("click", async () => {
  const status = document.querySelector("#brapi-token-status");
  try {
    await request("/investments/brapi-token", { method: "DELETE" });
    status.textContent = "Token removido deste computador.";
    status.classList.remove("error");
    await refreshMarketReturns();
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  }
});

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
    renderInvestmentPosition(data.position);
    void refreshMarketReturns();
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
document.querySelector("#cdi-months").addEventListener("change", refreshCDI);
document.addEventListener("investment-data-change", refreshInvestments);
document.addEventListener("finance-view-resume", refreshInvestments);
window.setInterval(refreshMarketReturns, 5 * 60 * 1000);
window.setInterval(refreshCDI, 5 * 60 * 1000);
syncAreaFromHash();
