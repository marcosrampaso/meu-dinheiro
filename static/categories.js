const categoryRange = document.querySelector("#categorization-range");
const categoryStatus = document.querySelector("#categorization-status");
const categoryPreview = document.querySelector("#categorization-preview");
const categoryList = document.querySelector("#categorization-list");
const suggestButton = document.querySelector("#suggest-categories");
const applyButton = document.querySelector("#apply-categories");
let categoryChoices = [];
let previewContext = null;
let statusRequestId = 0;

function categoryMonth() {
  return categoryRange.value === "month" ? monthInput.value : null;
}

function clearCategoryPreview() {
  previewContext = null;
  categoryPreview.hidden = true;
  categoryList.replaceChildren();
}

async function refreshCategoryStatus() {
  const requestId = ++statusRequestId;
  const month = categoryMonth();
  const scope = scopeInput.value;
  try {
    const suffix = `?scope=${encodeURIComponent(scope)}${month ? `&month=${encodeURIComponent(month)}` : ""}`;
    const data = await request(`/categorization/status${suffix}`);
    if (requestId !== statusRequestId) return;
    categoryChoices = data.categories;
    suggestButton.disabled = !data.configured || data.uncategorized_count === 0;
    categoryStatus.classList.remove("error");
    categoryStatus.textContent = !data.configured
      ? `${data.uncategorized_count} compras sem categoria. Para ativar, preencha OPENAI_API_KEY no arquivo .env do projeto e atualize a página.`
      : data.uncategorized_count
        ? `${data.uncategorized_count} compras sem categoria · até 40 sugestões por vez · ${data.model}.`
        : "Todas as compras deste período já têm categoria.";
  } catch (error) {
    if (requestId !== statusRequestId) return;
    suggestButton.disabled = true;
    categoryStatus.classList.add("error");
    categoryStatus.textContent = error.message;
  }
}

function renderCategorySuggestions(items, remainingCount) {
  categoryList.replaceChildren();
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "ai-category-row";
    row.dataset.id = String(item.id);
    const checkLabel = document.createElement("label");
    checkLabel.className = "ai-category-check";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.setAttribute("aria-label", `Aplicar categoria a ${item.description}`);
    checkLabel.append(checkbox);
    const details = document.createElement("div");
    details.className = "ai-category-details";
    const name = document.createElement("strong");
    name.textContent = item.description;
    const meta = document.createElement("small");
    meta.textContent = `${formatDate(item.occurred_on)} · ${money.format(item.amount_cents / 100)} · ${item.invoice_month ? "Cartão" : "Conta"}`;
    details.append(name, meta);
    const select = document.createElement("select");
    select.setAttribute("aria-label", `Categoria de ${item.description}`);
    for (const category of categoryChoices) {
      const option = document.createElement("option");
      option.value = category;
      option.textContent = category;
      select.append(option);
    }
    select.value = item.suggested_category;
    row.append(checkLabel, details, select);
    categoryList.append(row);
  }
  categoryPreview.hidden = false;
  categoryStatus.textContent = `${items.length} sugestões para revisar.${remainingCount ? ` Mais ${remainingCount} compras aguardam outra rodada.` : ""}`;
}

suggestButton.addEventListener("click", async () => {
  clearCategoryPreview();
  const context = { scope: scopeInput.value, month: categoryMonth() };
  let failed = false;
  suggestButton.disabled = true;
  categoryStatus.classList.remove("error");
  categoryStatus.textContent = "Consultando o modelo para até 40 descrições...";
  try {
    const result = await request("/categorization/suggest", {
      method: "POST", body: JSON.stringify(context),
    });
    if (context.scope !== scopeInput.value || context.month !== categoryMonth()) return;
    if (result.items.length) {
      previewContext = context;
      renderCategorySuggestions(result.items, result.remaining_count);
    } else {
      categoryStatus.textContent = "Não há compras sem categoria neste período.";
    }
  } catch (error) {
    failed = true;
    categoryStatus.classList.add("error");
    categoryStatus.textContent = error.message;
  } finally {
    if (previewContext) suggestButton.disabled = true;
    else if (failed) suggestButton.disabled = false;
    else await refreshCategoryStatus();
  }
});

applyButton.addEventListener("click", async () => {
  if (!previewContext || previewContext.scope !== scopeInput.value || previewContext.month !== categoryMonth()) {
    clearCategoryPreview();
    return;
  }
  const items = [...categoryList.querySelectorAll(".ai-category-row")]
    .filter(row => row.querySelector("input").checked)
    .map(row => ({ id: Number(row.dataset.id), category: row.querySelector("select").value }));
  if (!items.length) {
    categoryStatus.textContent = "Marque ao menos uma compra para aplicar.";
    return;
  }
  applyButton.disabled = true;
  try {
    const result = await request("/categorization/apply", {
      method: "POST", body: JSON.stringify({ scope: previewContext.scope, items }),
    });
    clearCategoryPreview();
    await refresh();
    showNotice(`${result.applied} compras categorizadas${result.skipped ? `; ${result.skipped} já tinham sido alteradas` : ""}.`);
  } catch (error) {
    categoryStatus.classList.add("error");
    categoryStatus.textContent = error.message;
  } finally {
    applyButton.disabled = false;
    await refreshCategoryStatus();
  }
});

document.querySelector("#cancel-categories").addEventListener("click", () => {
  clearCategoryPreview();
  refreshCategoryStatus();
});
categoryRange.addEventListener("change", () => { clearCategoryPreview(); refreshCategoryStatus(); });
document.addEventListener("finance-data-change", () => { clearCategoryPreview(); refreshCategoryStatus(); });
refreshCategoryStatus();
