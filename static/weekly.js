const weeklyGenerate = document.querySelector("#weekly-generate");
const weeklyStatus = document.querySelector("#weekly-status");
const weeklyReport = document.querySelector("#weekly-report");
let weeklyRequestId = 0;

function renderWeekly(data) {
  const period = data.period;
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
  for (const item of period.categories.filter(row => row.current_cents > 0).slice(0, 5)) {
    const row = document.createElement("div");
    row.className = "weekly-category";
    const name = document.createElement("span");
    name.textContent = item.category;
    const value = document.createElement("strong");
    value.textContent = money.format(item.current_cents / 100);
    row.append(name, value);
    categories.append(row);
  }
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
