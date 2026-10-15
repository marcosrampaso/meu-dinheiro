const newsRefreshButton = document.querySelector('#news-refresh');
const newsStatus = document.querySelector('#news-status');
const newsPositionLabel = document.querySelector('#news-position-label');
const newsReport = document.querySelector('#news-report');
const newsUseFinances = document.querySelector('#news-use-finances');
const newsModel = document.querySelector('#news-model');
let newsRequestId = 0;
let newsCanRefresh = false;
let newsBusy = false;
try {
  newsUseFinances.checked = localStorage.getItem('news-use-finances') !== 'false';
  const model = localStorage.getItem('news-model');
  if (['gpt-6.1-sol','gpt-6-luna'].includes(model)) newsModel.value = model;
} catch (_) { /* Preferences are optional. */ }

function newsNode(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function sourceLink(url, label = 'Ler na fonte ↗') {
  let parsed;
  try { parsed = new URL(url); } catch (_) { return newsNode('span', 'Fonte indisponível'); }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return newsNode('span', 'Fonte indisponível');
  const link = newsNode('a', label);
  link.href = parsed.href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function newsParagraph(text) { return newsNode('p', text); }
function newsMoney(cents) { return cents === null || cents === undefined ? 'Não informado' : money.format(cents / 100); }

function newsSection(title, subtitle) {
  const section = newsNode('section', undefined, 'news-section');
  const heading = newsNode('div', undefined, 'news-section-heading');
  heading.append(newsNode('h2', title));
  if (subtitle) heading.append(newsParagraph(subtitle));
  section.append(heading);
  return section;
}

function renderNewsContext(context, target) {
  const box = newsSection('Os números por trás da análise', 'Visão pessoal · valores locais, sem misturar o dinheiro da empresa. Saldo em conta não é sobra para investir.');
  const grid = newsNode('div', undefined, 'news-metrics');
  const reserve = context.reserve;
  for (const [label, value, note] of [
    ['Carteira XP', newsMoney(context.portfolio.value_cents), `Posição de ${formatDate(context.portfolio.as_of)}`],
    ['Saldo nas contas', newsMoney(context.cash.balance_cents), context.cash.oldest_balance_on ? `Saldo mais antigo: ${formatDate(context.cash.oldest_balance_on)}${context.cash.stale ? ' · desatualizado' : ''}` : 'Importe um saldo para conferir'],
    ['Reserva confirmada', reserve ? newsMoney(reserve.reserved_cents) : 'Não cadastrada', reserve ? `Meta: ${newsMoney(reserve.target_cents)} · saldo de ${formatDate(reserve.balance_as_of)}` : 'Cadastre seu plano para avaliar a proteção'],
    ['Gasto mensal observado', newsMoney(context.spending.average_cents), `${context.spending.months.length} meses de base · ${context.spending.quality === 'documented' ? 'cobertura documentada' : 'base parcial'}`],
  ]) {
    const metric = newsNode('div', undefined, 'news-metric');
    metric.append(newsNode('span', label), newsNode('strong', value), newsNode('small', note));
    grid.append(metric);
  }
  box.append(grid);
  const limits = newsNode('details', undefined, 'news-context-details');
  limits.append(newsNode('summary', 'Conferir dados usados e limitações'));
  limits.append(newsParagraph(`Compromissos de saída cadastrados até ${formatDate(context.cash.through)}: ${newsMoney(context.cash.known_expenses_cents)}. Capacidade de aporte livre: não calculada.`));
  if (reserve) limits.append(newsParagraph(`Aporte mensal planejado: ${newsMoney(reserve.monthly_cents)}. Falta para a meta da reserva: ${newsMoney(reserve.remaining_cents)}. O aporte é uma intenção, não saldo disponível.`));
  const assets = newsNode('ul');
  for (const asset of context.portfolio.assets) assets.append(newsNode('li', `${asset.asset}: ${newsMoney(asset.value_cents)} · ${asset.allocation_percent.toLocaleString('pt-BR')}% da carteira${asset.gain_cents !== null ? ` · diferença sobre custo: ${newsMoney(asset.gain_cents)}` : ' · custo desconhecido'}`));
  limits.append(assets);
  const categories = context.spending.categories.map(row => `${row.category}: ${newsMoney(row.average_cents)}`).join(' · ');
  if (categories) limits.append(newsParagraph(`Médias por categoria: ${categories}.`));
  const warnings = newsNode('ul');
  for (const warning of context.warnings) warnings.append(newsNode('li', warning));
  limits.append(warnings);
  box.append(limits);
  target.append(box);
}

function renderNewsActions(report, target) {
  if (!report.personalization) return;
  const section = newsSection('O que você pode fazer agora', report.personalization.summary);
  section.classList.add('news-personal-section');
  const grid = newsNode('div', undefined, 'news-action-grid');
  const destinations = {cash:['#financas','Conferir minhas contas'],reserve:['#planos','Revisar meu plano'],portfolio:['#investimentos','Conferir minha carteira'],spending:['#classificacao','Revisar meus gastos']};
  report.personalization.priorities.forEach((item, index) => {
    const card = newsNode('article', undefined, 'news-action-card');
    const title = newsNode('h3', item.title); title.setAttribute('data-private-value','');
    card.append(newsNode('span', `PRIORIDADE ${String(index+1).padStart(2,'0')}`, 'news-kicker'), title);
    card.append(newsNode('p', item.action, 'news-action-lead'), newsParagraph(item.reason));
    if (item.benefit) card.append(newsNode('p', `Benefício possível: ${item.benefit}`, 'news-action-benefit'));
    const details = newsNode('details');
    details.append(newsNode('summary', 'Quando agir e o que considerar'), newsParagraph(item.condition), newsParagraph(`Atenção: ${item.risk}`));
    if (item.source_url) details.append(sourceLink(item.source_url));
    card.append(details);
    const [href,label] = destinations[item.basis] || destinations.portfolio;
    const link = newsNode('a', `${label} →`, 'news-internal-link'); link.href = href;
    card.append(link); grid.append(card);
  });
  for (const node of section.querySelectorAll('p')) node.setAttribute('data-private-value','');
  for (const node of grid.querySelectorAll('p')) node.setAttribute('data-private-value','');
  section.append(grid);
  target.append(section);
}

function renderNewsArticles(report, target) {
  const verified = report.assets.filter(item => item.source_url);
  const section = newsSection('No radar da sua carteira', 'Uma leitura por ativo, com fato, impacto e próximo indicador. Abra os cenários para aprofundar.');
  const filter = newsNode('div', undefined, 'news-filters');
  filter.setAttribute('aria-label', 'Filtrar notícias por ativo');
  const grid = newsNode('div', undefined, 'news-grid');
  const cards = [];
  for (const [index,item] of verified.entries()) {
    const card = newsNode('article', undefined, 'news-card');
    card.dataset.asset = item.asset;
    card.style.setProperty('--news-tone', ['#5268f5','#008f9b','#bc4c92','#b96520'][index%4]);
    const cover = newsNode('div', undefined, 'news-card-cover');
    const type = {noticia:'Notícia',relatorio:'Relatório',referencia:'Referência oficial'}[item.source_kind] || 'Fonte de pesquisa';
    cover.append(newsNode('span', type, 'news-source-type'), newsNode('strong', item.asset, 'news-asset-name'));
    const body = newsNode('div', undefined, 'news-card-body');
    let host = 'Fonte'; try { host = new URL(item.source_url).hostname.replace(/^www\./,''); } catch (_) { /* Validated by server. */ }
    const age = item.published_on ? Math.floor((Date.now()-new Date(`${item.published_on}T12:00:00`).getTime())/86400000) : null;
    body.append(newsNode('p', `${host} · ${item.published_on ? formatDate(item.published_on) : 'Data não confirmada'}${age > 30 ? ' · fonte anterior a 30 dias' : ''}`, 'news-meta'));
    const title = newsNode('h3'); title.append(sourceLink(item.source_url,item.headline || 'Documento do ativo')); body.append(title);
    body.append(newsParagraph(item.what_changed || item.summary));
    if (item.why_it_matters) {
      const impact = newsNode('div', undefined, 'news-impact');
      impact.append(newsNode('strong', 'Como isso pode afetar seu investimento'),newsParagraph(item.why_it_matters)); body.append(impact);
    }
    if (item.watch_next) {
      const watch = newsNode('div', undefined, 'news-watch');
      watch.append(newsNode('strong', 'Próximo sinal para observar'),newsParagraph(item.watch_next)); body.append(watch);
    }
    const details = newsNode('details', undefined, 'news-scenarios');
    details.append(newsNode('summary','Cenários e riscos'));
    for (const [label,field] of [['0–3 meses','short_term'],['3–12 meses','medium_term'],['Mais de 12 meses','long_term'],['Principal risco','risk']]) {
      const row = newsNode('div', undefined, 'news-horizon');
      row.append(newsNode('strong',label),newsParagraph(item[field])); details.append(row);
    }
    body.append(details, sourceLink(item.source_url));
    card.append(cover,body); grid.append(card); cards.push(card);
  }
  for (const asset of ['Todos', ...new Set(verified.map(row => row.asset))]) {
    const button = newsNode('button', asset, 'news-filter'); button.type = 'button';
    button.setAttribute('aria-pressed',String(asset === 'Todos'));
    button.addEventListener('click', () => {
      filter.querySelectorAll('button').forEach(node => node.setAttribute('aria-pressed',String(node === button)));
      cards.forEach(card => { card.hidden = asset !== 'Todos' && card.dataset.asset !== asset; });
    });
    filter.append(button);
  }
  if (verified.length) section.append(filter,grid);
  const missing = report.assets.filter(item => !item.source_url);
  if (missing.length) section.append(newsNode('p', `Sem fonte suficiente nesta pesquisa: ${missing.map(row=>row.asset).join(', ')}. Não foram geradas previsões para esses ativos.`, 'news-empty-note'));
  target.append(section);
  if (report.opportunities.length) {
    const ideas = newsSection('Ideias para estudar', 'Avalie custos, liquidez, prazo e risco antes de decidir. Retorno maior pode exigir mais risco.');
    const ideaGrid = newsNode('div', undefined, 'news-opportunity-grid');
    for (const item of report.opportunities) {
      const card = newsNode('article', undefined, 'news-opportunity');
      const title = newsNode('h3'); title.append(sourceLink(item.source_url,item.title));
      card.append(title,newsParagraph(item.thesis),newsNode('p',`Risco: ${item.risk}`,'news-opportunity-risk'),sourceLink(item.source_url));
      ideaGrid.append(card);
    }
    ideas.append(ideaGrid); target.append(ideas);
  }
}

function renderNews(data) {
  newsReport.replaceChildren();
  document.querySelector('#news-context-preview').replaceChildren();
  if (!data.position) {
    newsPositionLabel.textContent = 'Importe uma posição da XP para começar.';
    newsCanRefresh = false; newsRefreshButton.disabled = true;
    newsStatus.textContent = 'Sua pesquisa será montada a partir dos ativos importados.';
    return;
  }
  const count = data.position.assets.length;
  newsPositionLabel.textContent = `Posição de ${formatDate(data.position.as_of)} · ${count} ${count === 1 ? 'ativo' : 'ativos'}${data.position.more_assets ? ` · ${data.position.more_assets} fora da pesquisa` : ''}`;
  newsCanRefresh = Boolean(data.chatgpt_connected);
  newsRefreshButton.disabled = !newsCanRefresh || newsBusy;
  if (data.financial_context) renderNewsContext(data.financial_context,document.querySelector('#news-context-preview'));
  if (!data.chatgpt_connected) {
    newsStatus.replaceChildren(document.createTextNode('Conecte o ChatGPT em '));
    const link = newsNode('a','Configurações'); link.href='#configuracoes'; newsStatus.append(link);
  } else newsStatus.textContent = data.report ? '' : 'Tudo pronto. Atualize a pesquisa para gerar os cards e as prioridades.';
  if (!data.report) {
    const empty = newsNode('div',undefined,'news-empty');
    empty.append(newsNode('span','SUA PRÓXIMA LEITURA','news-kicker'),newsNode('h2','Notícias com contexto. Decisões com fundamento.'),newsParagraph('Gere a pesquisa para ver o que mudou nos seus ativos, por que isso importa e quais ações vale avaliar.'));
    newsReport.append(empty); return;
  }
  const report = data.report;
  const stamp = newsNode('p',`Pesquisa de ${new Date(report.generated_at).toLocaleString('pt-BR')} · ${report.model} · ${report.uses_finances ? 'com totais pessoais da geração' : 'somente ativos'}`, 'news-report-stamp');
  newsReport.append(stamp);
  if (data.report_stale) newsReport.append(newsNode('p','Seus dados mudaram desde esta análise. Os cards abaixo usam o contexto anterior; atualize a pesquisa antes de decidir.','news-stale'));
  renderNewsActions(report,newsReport);
  if (report.financial_context && data.report_stale) {
    const old = newsNode('details',undefined,'news-context-details'); old.append(newsNode('summary','Ver os números usados no relatório anterior'));
    renderNewsContext(report.financial_context,old); newsReport.append(old);
  }
  renderNewsArticles(report,newsReport);
  newsReport.append(newsNode('p','Cenários são hipóteses, não garantia de rendimento. Links comprovam a origem da pesquisa, mas não validam automaticamente toda interpretação da IA.','news-footnote'));
  if (typeof financialPrivacy !== 'undefined') financialPrivacy.apply();
}

async function refreshNews() {
  if (newsView.hidden || newsBusy) return;
  const requestId = ++newsRequestId;
  try {
    const data = await request(`/investments/news?use_finances=${newsUseFinances.checked}`);
    if (requestId !== newsRequestId) return;
    renderNews(data); newsStatus.classList.remove('error');
  } catch (error) {
    if (requestId !== newsRequestId) return;
    newsStatus.textContent = error.message; newsStatus.classList.add('error');
  }
}

newsRefreshButton.addEventListener('click', async () => {
  if (newsBusy) return;
  newsBusy=true; ++newsRequestId;
  newsRefreshButton.disabled=true; newsModel.disabled=true; newsUseFinances.disabled=true;
  newsReport.setAttribute('aria-busy','true'); newsStatus.classList.remove('error');
  newsStatus.textContent = 'Pesquisando fontes e preparando sua análise… Isso pode levar alguns minutos.';
  try {
    const data = await request('/investments/news',{method:'POST',body:JSON.stringify({model:newsModel.value,use_finances:newsUseFinances.checked})});
    newsBusy=false; renderNews(data); showNotice('Pesquisa e análise atualizadas.');
  } catch (error) {
    newsStatus.textContent=error.message; newsStatus.classList.add('error');
  } finally {
    newsBusy=false; newsRefreshButton.disabled=!newsCanRefresh; newsModel.disabled=false; newsUseFinances.disabled=false;
    newsReport.removeAttribute('aria-busy');
  }
});
newsUseFinances.addEventListener('change',() => {
  try { localStorage.setItem('news-use-finances',String(newsUseFinances.checked)); } catch (_) { /* Optional. */ }
  refreshNews();
});
newsModel.addEventListener('change',() => {
  try { localStorage.setItem('news-model',newsModel.value); } catch (_) { /* Optional. */ }
});
document.addEventListener('news-view-resume',refreshNews);
document.addEventListener('investment-data-change',refreshNews);
document.addEventListener('finance-data-change',refreshNews);
if (!newsView.hidden) refreshNews();
