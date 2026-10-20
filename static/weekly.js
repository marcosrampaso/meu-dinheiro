const weeklyGenerate = document.querySelector("#weekly-generate");
const weeklyStatus = document.querySelector("#weekly-status");
const weeklyReport = document.querySelector("#weekly-report");
let weeklyRequestId = 0;
let weeklyData = null;
let weeklyCategory = null;

function weeklyElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderWeeklyTransactions() {
  if (!weeklyData) return;
  const rows = (weeklyData.transactions || []).filter(row => !weeklyCategory || row.category === weeklyCategory);
  const total = rows.reduce((sum, row) => sum + row.amount_cents, 0);
  document.querySelector('#weekly-list-title').textContent = weeklyCategory || 'Gastos da semana';
  document.querySelector('#weekly-list-summary').textContent = `${rows.length} gasto${rows.length === 1 ? '' : 's'} · ${money.format(total / 100)}`;
  document.querySelector('#weekly-clear-filter').hidden = !weeklyCategory;
  const target = document.querySelector('#weekly-transactions');
  target.replaceChildren();
  if (!rows.length) {
    target.append(weeklyElement('p', 'weekly-empty', 'Nenhum gasto registrado nesta semana.'));
    return;
  }
  const days = new Map();
  for (const row of rows) {
    if (!days.has(row.occurred_on)) days.set(row.occurred_on, []);
    days.get(row.occurred_on).push(row);
  }
  for (const [day, entries] of days) {
    const section = weeklyElement('section', 'weekly-day');
    const header = weeklyElement('h4', 'weekly-day-heading');
    const date = new Date(day + 'T12:00:00');
    const label = date.toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit' });
    const time = weeklyElement('time', '', label);
    time.dateTime = day;
    header.append(time, weeklyElement('span', '', money.format(entries.reduce((sum, row) => sum + row.amount_cents, 0) / 100)));
    const list = weeklyElement('ul', 'weekly-day-list');
    for (const entry of entries) {
      const item = weeklyElement('li', 'weekly-transaction');
      const details = weeklyElement('div', 'weekly-transaction-details');
      details.append(weeklyElement('strong', '', entry.description));
      const origin = entry.payment_type === 'credit' ? 'Cartão' : 'Conta · débito / PIX';
      const meta = [entry.category, origin];
      if (entry.pending) meta.push('Provisório');
      if (weeklyData.period.scope === 'all') meta.push(entry.scope === 'business' ? 'PJ' : 'Pessoal');
      details.append(weeklyElement('small', '', meta.join(' · ')));
      item.append(details, weeklyElement('strong', 'weekly-transaction-value', money.format(entry.amount_cents / 100)));
      list.append(item);
    }
    section.append(header, list);
    target.append(section);
  }
}

function selectWeeklyCategory(category) {
  weeklyCategory = category;
  for (const button of document.querySelectorAll('.weekly-category-bar')) {
    button.setAttribute('aria-pressed', String(button.dataset.category === category));
    button.classList.toggle('is-muted', Boolean(category) && button.dataset.category !== category);
  }
  renderWeeklyTransactions();
}

document.querySelector('#weekly-clear-filter').addEventListener('click', () => selectWeeklyCategory(null));

function renderWeekly(data) {
  const period = data.period;
  weeklyData = data;
  if (!period.categories.some(row => row.category === weeklyCategory && row.current_cents > 0)) weeklyCategory = null;
  document.querySelector("#weekly-period").textContent =
    `${formatDate(period.starts_on)} a ${formatDate(period.ends_on)} · ${scopeInput.selectedOptions[0].textContent}`;
  document.querySelector("#weekly-total").textContent = money.format(period.current_total_cents / 100);
  const comparison = document.querySelector("#weekly-comparison");
  comparison.textContent = `Semana anterior: ${money.format(period.previous_total_cents / 100)}. ` +
    (period.previous_total_cents ?
      `${period.current_total_cents > period.previous_total_cents ? "Alta" : period.current_total_cents < period.previous_total_cents ? "Queda" : "Mesmo valor"} de ${money.format(Math.abs(period.current_total_cents - period.previous_total_cents) / 100)}.` :
      "Sem gastos registrados para comparação.");
  const highlights = document.querySelector("#weekly-highlights");
  highlights.replaceChildren();
  const facts = [];
  if (period.highlights.biggest_increase) facts.push(`Maior alta: ${period.highlights.biggest_increase.category} · ${money.format(period.highlights.biggest_increase.delta_cents / 100)}`);
  if (period.highlights.biggest_decrease) facts.push(`Maior queda: ${period.highlights.biggest_decrease.category} · ${money.format(Math.abs(period.highlights.biggest_decrease.delta_cents) / 100)}`);
  if (period.highlights.uncategorized_cents) facts.push(`Sem categoria: ${money.format(period.highlights.uncategorized_cents / 100)}`);
  for (const fact of facts) {
    const item = document.createElement("span");
    item.textContent = fact;
    highlights.append(item);
  }
  const categories = document.querySelector("#weekly-categories");
  categories.replaceChildren();
  const current = period.categories.filter(row => row.current_cents > 0);
  const maximum = Math.max(1, ...current.map(row => row.current_cents));
  for (const item of current) {
    const button = weeklyElement('button', 'weekly-category-bar');
    button.type = 'button';
    button.dataset.category = item.category;
    const share = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(100 * item.current_cents / period.current_total_cents);
    button.title = `${item.category}: ${money.format(item.current_cents / 100)} · ${share}% da semana`;
    button.setAttribute('aria-label', `${button.title}. Filtrar os gastos desta categoria.`);
    const heading = weeklyElement('span', 'weekly-bar-heading');
    heading.append(weeklyElement('span', '', item.category), weeklyElement('strong', '', money.format(item.current_cents / 100)));
    const track = weeklyElement('span', 'weekly-bar-track');
    track.setAttribute('aria-hidden', 'true');
    const fill = weeklyElement('span', 'weekly-bar-fill');
    fill.style.width = `${100 * item.current_cents / maximum}%`;
    fill.style.backgroundColor = categoryColors[item.category] || '#94a3b8';
    track.append(fill);
    button.append(heading, track, weeklyElement('small', '', `${share}% da semana`));
    button.addEventListener('click', () => selectWeeklyCategory(weeklyCategory === item.category ? null : item.category));
    categories.append(button);
  }
  if (!current.length) categories.append(weeklyElement('p', 'weekly-empty', 'Sem gastos para montar o gráfico.'));
  selectWeeklyCategory(weeklyCategory);
  weeklyGenerate.disabled = !data.chatgpt_connected || !period.current_total_cents;
  weeklyReport.replaceChildren();
  if (data.report) {
    const title = document.createElement("h3");
    title.textContent = data.report.headline;
    const list = document.createElement("ul");
    for (const insight of data.report.insights) {
      const item = document.createElement("li");
      item.textContent = insight;
      list.append(item);
    }
    const stamp = document.createElement("small");
    stamp.textContent = `Gerado em ${new Date(data.report.generated_at).toLocaleString("pt-BR")} com ${data.report.model}.`;
    weeklyReport.append(title, list, stamp);
  }
  weeklyStatus.textContent = !data.chatgpt_connected ? "Conecte o ChatGPT na aba Configurações para gerar insights." :
    !period.current_total_cents ? "Sem gastos registrados na última semana concluída." :
      data.report ? "" : "Os números estão prontos. Gere a análise quando quiser.";
  weeklyStatus.classList.remove("error");
}

async function refreshWeekly() {
  if (financesView.hidden) return;
  const requestId = ++weeklyRequestId;
  const scope = scopeInput.value;
  try {
    const data = await request(`/weekly-summary?scope=${encodeURIComponent(scope)}`);
    if (requestId !== weeklyRequestId || scope !== scopeInput.value) return;
    renderWeekly(data);
  } catch (error) {
    if (requestId !== weeklyRequestId) return;
    weeklyStatus.textContent = error.message;
    weeklyStatus.classList.add("error");
  }
}

weeklyGenerate.addEventListener("click", async () => {
  const scope = scopeInput.value;
  weeklyGenerate.disabled = true;
  weeklyStatus.textContent = "Gerando insights com GPT-6 Luna...";
  weeklyStatus.classList.remove("error");
  try {
    const data = await request("/weekly-summary", { method: "POST", body: JSON.stringify({ scope }) });
    if (scope === scopeInput.value) renderWeekly(data);
  } catch (error) {
    weeklyStatus.textContent = error.message;
    weeklyStatus.classList.add("error");
    weeklyGenerate.disabled = false;
  }
});

document.addEventListener("finance-data-change", refreshWeekly);
document.addEventListener("finance-scope-change", refreshWeekly);
document.addEventListener("finance-view-resume", refreshWeekly);
refreshWeekly();
