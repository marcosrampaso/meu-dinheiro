const cardPurchaseForm = document.querySelector("#card-purchase-form");
const cardBankNames = { inter: "Inter", bb: "Banco do Brasil" };
let knownCardSources = [];

function updateKnownCardAccounts(selectFirst = false) {
  const bank = cardBankNames[cardPurchaseForm.elements.bank.value];
  const accounts = knownCardSources
    .filter((source) => source.startsWith(`${bank} · `))
    .map((source) => source.slice(bank.length + 3));
  const list = document.querySelector("#known-card-accounts");
  list.replaceChildren(...accounts.map((account) => {
    const option = document.createElement("option");
    option.value = account;
    return option;
  }));
  if (selectFirst) cardPurchaseForm.elements.account.value = accounts[0] || "";
}

async function loadKnownCards(selectFirst = false) {
  try {
    const scope = cardPurchaseForm.elements.scope.value;
    const invoices = await request(`/invoices?scope=${encodeURIComponent(scope)}`);
    knownCardSources = [...new Set(invoices.items.map((item) => item.source))];
    updateKnownCardAccounts(selectFirst);
  } catch (error) {
    showNotice(error.message, true);
  }
}

cardPurchaseForm.elements.scope.value = scopeInput.value === "business" ? "business" : "personal";
cardPurchaseForm.elements.invoice_month.value = monthInput.value;
cardPurchaseForm.elements.occurred_on.value = todayLocal();
loadKnownCards(true);

cardPurchaseForm.elements.bank.addEventListener("change", () => updateKnownCardAccounts(true));
cardPurchaseForm.elements.scope.addEventListener("change", () => loadKnownCards(true));
document.addEventListener("finance-scope-change", () => {
  cardPurchaseForm.elements.scope.value = scopeInput.value === "business" ? "business" : "personal";
  loadKnownCards(true);
});
monthInput.addEventListener("change", () => {
  cardPurchaseForm.elements.invoice_month.value = monthInput.value;
});

cardPurchaseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#save-card-purchase");
  const data = {
    bank: cardPurchaseForm.elements.bank.value,
    scope: cardPurchaseForm.elements.scope.value,
    account: cardPurchaseForm.elements.account.value,
    invoice_month: cardPurchaseForm.elements.invoice_month.value,
    occurred_on: cardPurchaseForm.elements.occurred_on.value,
    amount: cardPurchaseForm.elements.amount.value,
    description: cardPurchaseForm.elements.description.value,
    category: cardPurchaseForm.elements.category.value,
  };
  button.disabled = true;
  try {
    await request("/card-purchases", { method: "POST", body: JSON.stringify(data) });
    cardPurchaseForm.elements.description.value = "";
    cardPurchaseForm.elements.amount.value = "";
    cardPurchaseForm.elements.category.value = "";
    monthInput.value = data.occurred_on.slice(0, 7);
    scopeInput.value = data.scope;
    await Promise.all([refresh(), refreshInvoices(), loadKnownCards()]);
    showNotice(`Compra adicionada à fatura ${data.invoice_month}.`);
    cardPurchaseForm.elements.description.focus();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});
