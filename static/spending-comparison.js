let comparisonRequestId = 0;
const comparisonTabs = [document.querySelector('#weekly-tab'), document.querySelector('#comparison-tab')];

function comparisonMonth(month) {
  return new Date(month + '-01T12:00:00').toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' });
}

function renderSpendingComparison(data) {
  const periods = data.periods;
  document.querySelector('#comparison-title').textContent = `Seus gastos até o dia ${data.day}`;
  document.querySelector('#comparison-period').textContent = `${formatDate(data.as_of)} · ${scopeInput.selectedOptions[0].textContent}`;
  const totals = document.querySelector('#comparison-totals');
  const chart = document.querySelector('#comparison-chart');
  totals.replaceChildren();
  chart.replaceChildren();
  const maximum = Math.max(1, ...periods.map(period => period.total_cents));
  const colors = ['#6954e8', '#10a5bd', '#f59e0b'];
  periods.forEach((period, index) => {
    const card = weeklyElement('article', 'comparison-total');
    card.append(weeklyElement('h3', '', comparisonMonth(period.month)),
      weeklyElement('strong', '', period.count ? money.format(period.total_cents / 100) : 'Sem gastos registrados'),
      weeklyElement('small', '', `${formatDate(period.starts_on)} a ${formatDate(period.ends_on)} · ${period.count} gastos`));
    card.append(weeklyElement('small', '', `Cartão: ${money.format(period.credit_cents / 100)} · Conta: ${money.format(period.account_cents / 100)}`));
    if (index && period.change_percent !== null) {
      const change = weeklyElement('p', 'comparison-change');
      const percent = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(Math.abs(period.change_percent));
      change.textContent = period.change_cents === 0 ? 'Mesmo total que neste mês.' :
        `Neste mês: ${money.format(Math.abs(period.change_cents) / 100)} ${period.change_cents > 0 ? 'a mais' : 'a menos'} (${percent}%).`;
      card.append(change);
    } else if (index) {
      card.append(weeklyElement('p', 'comparison-change', 'Sem base de gastos para calcular a variação.'));
    } else {
      card.append(weeklyElement('p', 'comparison-change', 'Mês atual · ponto de comparação'));
    }
    totals.append(card);
    const row = weeklyElement('div', 'comparison-chart-row');
    row.append(weeklyElement('span', '', comparisonMonth(period.month)));
    const track = weeklyElement('span', 'weekly-bar-track');
    const fill = weeklyElement('span', 'weekly-bar-fill');
    fill.style.width = `${100 * period.total_cents / maximum}%`;
    fill.style.backgroundColor = colors[index];
    track.append(fill);
    row.append(track, weeklyElement('strong', '', money.format(period.total_cents / 100)));
    chart.append(row);
  });
  const categories = [...new Set(periods.flatMap(period => Object.keys(period.categories)))];
  categories.sort((a, b) => (periods[0].categories[b] || 0) - (periods[0].categories[a] || 0) || a.localeCompare(b));
  const target = document.querySelector('#comparison-categories');
  target.replaceChildren();
  if (!categories.length) {
    target.append(weeklyElement('p', 'weekly-empty', 'Sem gastos registrados nestes intervalos.'));
    return;
  }
  const table = weeklyElement('table', 'comparison-table');
  const caption = weeklyElement('caption', 'sr-only', `Gastos por categoria até o dia ${data.day} de cada mês`);
  const head = weeklyElement('thead');
  const heading = weeklyElement('tr');
  const categoryHeading = weeklyElement('th', '', 'Categoria');
  categoryHeading.scope = 'col';
  heading.append(categoryHeading);
  for (const period of periods) {
    const cell = weeklyElement('th', '', comparisonMonth(period.month));
    cell.scope = 'col';
    heading.append(cell);
  }
  head.append(heading);
  const body = weeklyElement('tbody');
  for (const category of categories) {
    const row = weeklyElement('tr');
    const label = weeklyElement('th', '', category);
    label.scope = 'row';
    row.append(label);
    for (const period of periods) row.append(weeklyElement('td', '', money.format((period.categories[category] || 0) / 100)));
    body.append(row);
  }
  table.append(caption, head, body);
  target.append(table);
}

async function refreshSpendingComparison() {
  if (financesView.hidden || document.querySelector('#comparison-content').hidden) return;
  const requestId = ++comparisonRequestId;
  const scope = scopeInput.value;
  const status = document.querySelector('#comparison-status');
  status.textContent = 'Comparando os mesmos dias dos três meses...';
  status.classList.remove('error');
  try {
    const data = await request(`/spending-comparison?scope=${encodeURIComponent(scope)}`);
    if (requestId !== comparisonRequestId || scope !== scopeInput.value) return;
    renderSpendingComparison(data);
    status.textContent = '';
  } catch (error) {
    if (requestId !== comparisonRequestId) return;
    for (const id of ['comparison-totals', 'comparison-chart', 'comparison-categories']) document.getElementById(id).replaceChildren();
    status.textContent = error.message;
    status.classList.add('error');
  }
}

function selectSpendingAnalysis(index, focus = false) {
  comparisonTabs.forEach((tab, position) => {
    tab.setAttribute('aria-selected', String(position === index));
    tab.tabIndex = position === index ? 0 : -1;
    document.getElementById(tab.getAttribute('aria-controls')).hidden = position !== index;
  });
  if (focus) comparisonTabs[index].focus();
  if (index === 1) refreshSpendingComparison();
}

comparisonTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => selectSpendingAnalysis(index));
  tab.addEventListener('keydown', event => {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    selectSpendingAnalysis(event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index, true);
  });
});

for (const event of ['finance-data-change', 'finance-scope-change', 'finance-view-resume']) {
  document.addEventListener(event, refreshSpendingComparison);
}
