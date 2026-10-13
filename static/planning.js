const budgetForm = document.querySelector("#budget-form");
const calendarForm = document.querySelector("#calendar-form");
const cardDueForm = document.querySelector("#card-due-form");
let planningRequestId = 0;

function planningStatus(id, message, isError = false) {
  const target = document.getElementById(id);
  target.textContent = message;
  target.classList.toggle("error", isError);
}

function renderHealth(data) {
  const badge = document.querySelector("#health-badge");
  badge.textContent = data.has_gaps ? "Dados a conferir" : "Sem lacunas detectadas";
  badge.classList.toggle("good", !data.has_gaps);
  const target = document.querySelector("#health-items");
  target.replaceChildren();
  if (!data.accounts.length && !data.invoices.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhum extrato de conta ou cartão conhecido nesta visão.";
    target.append(empty);
  }
  for (const account of data.accounts) {
    const item = document.createElement("div");
    item.className = "health-item" + (account.status === "full" ? "" : " attention");
    const name = document.createElement("strong");
    name.textContent = `${account.source}${data.scope === "all" ? account.scope === "business" ? " · PJ" : " · Pessoal" : ""}`;
    const detail = document.createElement("small");
    detail.textContent = account.status === "full" ? "Extrato cobre o período até a data de referência." :
      `Extrato cobre ${account.covered_days} de ${account.expected_days} dias do período.`;
    item.append(name, detail);
    target.append(item);
  }
  for (const invoice of data.invoices) {
    const item = document.createElement("div");
    item.className = "health-item" + (invoice.status === "registered" ? "" : " attention");
    const name = document.createElement("strong");
    name.textContent = `${invoice.source}${data.scope === "all" ? invoice.scope === "business" ? " · PJ" : " · Pessoal" : ""}`;
    const detail = document.createElement("small");
    detail.textContent = invoice.status === "registered" ?
      `${invoice.imported_count} itens importados${invoice.manual_count ? ` e ${invoice.manual_count} manuais` : ""}; confira o fechamento da fatura.` :
      invoice.status === "manual" ? `${invoice.manual_count} itens manuais; PDF ainda não registrado.` :
        "Sem fatura registrada para este mês.";
    item.append(name, detail);
    target.append(item);
  }
  document.querySelector("#health-note").textContent =
    `${data.through ? `Conferido até ${formatDate(data.through)}. ` : ""}${data.note}`;
}

function renderBudgets(data) {
  const datalist = document.querySelector("#budget-categories");
  datalist.replaceChildren();
  for (const category of data.categories) {
    const option = document.createElement("option");
    option.value = category;
    datalist.append(option);
  }
  const target = document.querySelector("#budget-list");
  target.replaceChildren();
  if (!data.budgets.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhum limite definido. Escolha uma categoria para começar.";
    target.append(empty);
  }
  for (const budget of data.budgets) {
    const row = document.createElement("div");
    row.className = "budget-row";
    const header = document.createElement("div");
    header.className = "budget-row-head";
    const title = document.createElement("strong");
    title.textContent = `${budget.category}${data.scope === "all" ? budget.scope === "business" ? " · PJ" : " · Pessoal" : ""}`;
    const values = document.createElement("span");
    values.textContent = `${money.format(budget.spent_cents / 100)} / ${money.format(budget.limit_cents / 100)}`;
    header.append(title, values);
    const track = document.createElement("div");
    track.className = "budget-track";
    const fill = document.createElement("div");
    fill.className = "budget-fill";
    const ratio = budget.spent_cents / budget.limit_cents;
    fill.style.width = `${Math.min(100, ratio * 100)}%`;
    fill.classList.toggle("warning", ratio >= .8 && ratio < 1);
    fill.classList.toggle("over", ratio >= 1);
    track.append(fill);
    const footer = document.createElement("small");
    footer.textContent = `${Math.round(ratio * 100)}% do limite usado${ratio > 1 ? ` · ${money.format((budget.spent_cents - budget.limit_cents) / 100)} acima` : ""}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remover limite";
    remove.addEventListener("click", async () => {
      try {
        await request(`/budgets?scope=${budget.scope}&category=${encodeURIComponent(budget.category)}`, { method: "DELETE" });
        await refreshPlanning();
      } catch (error) { planningStatus("budget-status", error.message, true); }
    });
    row.append(header, track, footer, remove);
    target.append(row);
  }
}

function renderCalendar(data) {
  const projection = document.querySelector("#calendar-projection");
  projection.textContent = data.projected_balance_cents === null ? "Saldo indisponível" :
    money.format(data.projected_balance_cents / 100);
  const events = document.querySelector("#calendar-events");
  events.replaceChildren();
  if (!data.events.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhum vencimento nas próximas seis semanas.";
    events.append(empty);
  }
  for (const event of data.events) {
    const item = document.createElement("div");
    item.className = "calendar-event " + event.kind;
    const detail = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = event.description;
    const when = document.createElement("small");
    when.textContent = `${formatDate(event.due_on)}${data.scope === "all" ? event.scope === "business" ? " · PJ" : " · Pessoal" : ""}${event.partial ? " · fatura parcial" : ""}`;
    detail.append(title, when);
    const amount = document.createElement("strong");
    amount.textContent = `${event.kind === "income" ? "+" : "−"} ${money.format(event.amount_cents / 100)}`;
    item.append(detail, amount);
    events.append(item);
  }
  const commitments = document.querySelector("#calendar-commitments");
  commitments.replaceChildren();
  for (const item of data.commitments) {
    const row = document.createElement("div");
    row.className = "calendar-row";
    const title = document.createElement("span");
    title.textContent = `${item.description} · dia ${item.due_day} · ${money.format(item.amount_cents / 100)}${data.scope === "all" ? item.scope === "business" ? " · PJ" : " · Pessoal" : ""}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remover";
    remove.addEventListener("click", async () => {
      try {
        await request(`/calendar/${item.id}`, { method: "DELETE" });
        await refreshPlanning();
      } catch (error) { planningStatus("calendar-status", error.message, true); }
    });
    row.append(title, remove);
    commitments.append(row);
  }
  const cardSelect = cardDueForm.elements.card;
  const selectedCard = cardSelect.value;
  cardSelect.replaceChildren();
  for (const card of data.known_cards) {
    const option = document.createElement("option");
    option.value = JSON.stringify(card);
    option.textContent = `${card.source}${data.scope === "all" ? card.scope === "business" ? " · PJ" : " · Pessoal" : ""}`;
    cardSelect.append(option);
  }
  if ([...cardSelect.options].some(option => option.value === selectedCard)) cardSelect.value = selectedCard;
  cardDueForm.querySelector("button[type=submit]").disabled = !data.known_cards.length;
  const rules = document.querySelector("#card-due-rules");
  rules.replaceChildren();
  for (const rule of data.card_rules) {
    const row = document.createElement("div");
    row.className = "calendar-row";
    const title = document.createElement("span");
    title.textContent = `${rule.source} · dia ${rule.due_day} ${rule.month_offset ? "do mês seguinte" : "do mês da fatura"}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remover";
    remove.addEventListener("click", async () => {
      try {
        await request(`/calendar/cards/${rule.id}`, { method: "DELETE" });
        await refreshPlanning();
      } catch (error) { planningStatus("calendar-status", error.message, true); }
    });
    row.append(title, remove);
    rules.append(row);
  }
  document.querySelector("#calendar-note").textContent =
    `${data.opening_balance_as_of ? `Saldo base de ${formatDate(data.opening_balance_as_of)}${data.balance_stale ? " (pode estar desatualizado)" : ""}. ` : "Sem saldo de extrato para iniciar a projeção. "}${data.note}`;
}

async function refreshPlanning() {
  if (financesView.hidden) return;
  const requestId = ++planningRequestId;
  const month = monthInput.value, scope = scopeInput.value;
  try {
    const [health, budgets, calendar] = await Promise.all([
      request(`/data-health?month=${encodeURIComponent(month)}&scope=${scope}`),
      request(`/budgets?month=${encodeURIComponent(month)}&scope=${scope}`),
      request(`/calendar?scope=${scope}`),
    ]);
    if (requestId !== planningRequestId || month !== monthInput.value || scope !== scopeInput.value) return;
    renderHealth(health); renderBudgets(budgets); renderCalendar(calendar);
  } catch (error) {
    planningStatus("budget-status", error.message, true);
  }
}

budgetForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = budgetForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await request("/budgets", { method: "POST", body: JSON.stringify({
      scope: budgetForm.elements.scope.value, category: budgetForm.elements.category.value,
      limit: budgetForm.elements.limit.value,
    }) });
    budgetForm.elements.limit.value = "";
    planningStatus("budget-status", "Limite mensal salvo.");
    await refreshPlanning();
  } catch (error) { planningStatus("budget-status", error.message, true); }
  finally { button.disabled = false; }
});

calendarForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = calendarForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await request("/calendar", { method: "POST", body: JSON.stringify({
      scope: calendarForm.elements.scope.value, kind: calendarForm.elements.kind.value,
      description: calendarForm.elements.description.value, amount: calendarForm.elements.amount.value,
      due_day: Number(calendarForm.elements.due_day.value),
      starts_month: calendarForm.elements.starts_month.value,
    }) });
    calendarForm.elements.description.value = "";
    calendarForm.elements.amount.value = "";
    planningStatus("calendar-status", "Recorrência adicionada.");
    await refreshPlanning();
  } catch (error) { planningStatus("calendar-status", error.message, true); }
  finally { button.disabled = false; }
});

cardDueForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = cardDueForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const card = JSON.parse(cardDueForm.elements.card.value);
    await request("/calendar/cards", { method: "POST", body: JSON.stringify({
      ...card, due_day: Number(cardDueForm.elements.due_day.value),
      month_offset: Number(cardDueForm.elements.month_offset.value),
    }) });
    planningStatus("calendar-status", "Vencimento da fatura salvo.");
    await refreshPlanning();
  } catch (error) { planningStatus("calendar-status", error.message, true); }
  finally { button.disabled = false; }
});

calendarForm.elements.starts_month.value = todayLocal().slice(0, 7);
function planningScopeChanged() {
  const current = scopeInput.value === "business" ? "business" : "personal";
  budgetForm.elements.scope.value = current;
  calendarForm.elements.scope.value = current;
  refreshPlanning();
}
monthInput.addEventListener("change", refreshPlanning);
document.addEventListener("finance-scope-change", planningScopeChanged);
document.addEventListener("finance-data-change", refreshPlanning);
document.addEventListener("finance-view-resume", refreshPlanning);
refreshPlanning();
