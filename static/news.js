const newsRefreshButton = document.querySelector("#news-refresh");
const newsStatus = document.querySelector("#news-status");
const newsPositionLabel = document.querySelector("#news-position-label");
const newsReport = document.querySelector("#news-report");
let newsRequestId = 0;
let newsCanRefresh = false;

function sourceLink(url, label = "Abrir fonte") {
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = label;
  return link;
}

function newsParagraph(text) {
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  return paragraph;
}

function renderNews(data) {
  newsReport.replaceChildren();
  if (!data.position) {
    newsPositionLabel.textContent = "Importe uma posição detalhada da XP para pesquisar.";
    newsRefreshButton.disabled = true;
    newsCanRefresh = false;
    newsStatus.textContent = "";
    return;
  }
  const count = data.position.assets.length;
  newsPositionLabel.textContent = `Posição de ${formatDate(data.position.as_of)} · ${count} ${count === 1 ? "ativo" : "ativos"} para pesquisar${data.position.more_assets ? ` · ${data.position.more_assets} fora da pesquisa` : ""}`;
  newsCanRefresh = Boolean(data.chatgpt_connected);
  newsRefreshButton.disabled = !newsCanRefresh;
  if (!data.chatgpt_connected) {
    newsStatus.textContent = "Conecte o plano ChatGPT na seção de categorias da aba Finanças para atualizar a pesquisa.";
  } else {
    newsStatus.textContent = data.report ? "" : "Clique em Atualizar pesquisa para buscar notícias recentes.";
  }
  if (!data.report) return;
  const report = data.report;
  const stamp = document.createElement("p");
  stamp.className = "news-meta";
  stamp.textContent = `Pesquisa gerada em ${new Date(report.generated_at).toLocaleString("pt-BR")} com ${report.model}. Fontes e cenários podem mudar.`;
  newsReport.append(stamp);
  for (const item of report.assets) {
    const card = document.createElement("section");
    card.className = "panel news-card";
    const header = document.createElement("div");
    header.className = "panel-header";
    const title = document.createElement("h2");
    title.textContent = item.asset;
    header.append(title);
    card.append(header);
    if (item.source_url) {
      const article = document.createElement("div");
      article.className = "news-article";
      article.append(sourceLink(item.source_url, item.headline || "Fonte da pesquisa"));
      if (item.published_on) {
        const date = document.createElement("small");
        date.className = "news-meta";
        date.textContent = ` · ${formatDate(item.published_on)}`;
        article.append(date);
      } else {
        const date = document.createElement("small");
        date.className = "news-meta";
        date.textContent = " · Sem data de publicação confirmada";
        article.append(date);
      }
      article.append(newsParagraph(item.summary));
      card.append(article);
    } else {
      card.append(newsParagraph("Não encontramos uma fonte recente verificável para este ativo."));
    }
    for (const [label, field] of [
      ["Curto prazo", "short_term"], ["Médio prazo", "medium_term"],
      ["Longo prazo", "long_term"], ["Principal risco", "risk"],
    ]) {
      const row = document.createElement("div");
      row.className = "news-horizon";
      const heading = document.createElement("strong");
      heading.textContent = label;
      row.append(heading, newsParagraph(item[field]));
      card.append(row);
    }
    if (item.source_url) {
      const citation = document.createElement("div");
      citation.className = "news-meta";
      citation.append(sourceLink(item.source_url, "Fonte dos fatos e cenários"));
      card.append(citation);
    }
    newsReport.append(card);
  }
  const opportunities = document.createElement("section");
  opportunities.className = "panel news-opportunities";
  const title = document.createElement("h2");
  title.textContent = "Oportunidades para estudar";
  opportunities.append(title);
  opportunities.append(newsParagraph("Ideias para acompanhar, com tese e risco. Não são ordens de investimento."));
  if (!report.opportunities.length) {
    opportunities.append(newsParagraph("Nenhuma oportunidade com fonte verificável nesta pesquisa."));
  }
  for (const item of report.opportunities) {
    const row = document.createElement("div");
    row.className = "news-opportunity";
    row.append(sourceLink(item.source_url, item.title));
    row.append(newsParagraph(item.thesis));
    row.append(newsParagraph(`Risco: ${item.risk}`));
    opportunities.append(row);
  }
  newsReport.append(opportunities);
}

async function refreshNews() {
  if (newsView.hidden) return;
  const requestId = ++newsRequestId;
  try {
    const data = await request("/investments/news");
    if (requestId !== newsRequestId) return;
    renderNews(data);
    newsStatus.classList.remove("error");
  } catch (error) {
    if (requestId !== newsRequestId) return;
    newsStatus.textContent = error.message;
    newsStatus.classList.add("error");
  }
}

newsRefreshButton.addEventListener("click", async () => {
  newsRefreshButton.disabled = true;
  newsStatus.classList.remove("error");
  newsStatus.textContent = "Pesquisando fontes recentes com GPT-6 Luna...";
  try {
    const data = await request("/investments/news", {method: "POST", body: JSON.stringify({})});
    renderNews(data);
    showNotice("Pesquisa de investimentos atualizada.");
  } catch (error) {
    newsStatus.textContent = error.message;
    newsStatus.classList.add("error");
  } finally {
    newsRefreshButton.disabled = !newsCanRefresh;
  }
});

document.addEventListener("news-view-resume", refreshNews);
document.addEventListener("investment-data-change", refreshNews);
if (!newsView.hidden) refreshNews();
