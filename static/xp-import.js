const xpForm = document.querySelector("#xp-import-form");
const xpFile = xpForm.elements.file;
const xpStatus = document.querySelector("#xp-import-status");
const xpPreview = document.querySelector("#xp-import-preview");
const xpImportButton = document.querySelector("#xp-import-button");
let xpPayload = null;

function clearXpPreview() {
  xpPayload = null;
  xpImportButton.disabled = true;
  xpPreview.hidden = true;
  xpPreview.replaceChildren();
}

function setXpStatus(message, error = false) {
  xpStatus.textContent = message;
  xpStatus.classList.toggle("error", error);
}

xpFile.addEventListener("change", () => { clearXpPreview(); setXpStatus(""); });

xpForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearXpPreview();
  const file = xpFile.files[0];
  if (!file) return;
  if (file.size > 2_000_000 || !file.name.toLowerCase().endsWith(".csv")) {
    setXpStatus("Selecione um CSV da XP com até 2 MB.", true);
    return;
  }
  const button = xpForm.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const content = await file.text();
    const payload = { filename: file.name, content };
    const result = await request("/investments/imports/preview", {
      method: "POST", body: JSON.stringify(payload),
    });
    const title = document.createElement("strong");
    title.textContent = `${result.count} lançamentos XP; ${result.already_imported} já salvos. ${result.matches.length} transferências do Inter confirmadas.`;
    xpPreview.append(title);
    for (const match of result.matches) {
      const row = document.createElement("div");
      row.className = "preview-row";
      const description = document.createElement("span");
      description.textContent = `${formatDate(match.occurred_on)} · Inter ${match.scope === "business" ? "PJ" : "Pessoal"} · ${match.description}`;
      const amount = document.createElement("b");
      amount.textContent = money.format(match.amount_cents / 100);
      row.append(description, amount);
      xpPreview.append(row);
    }
    xpPreview.hidden = false;
    xpPayload = payload;
    xpImportButton.disabled = false;
    setXpStatus("Confira os pares identificados e confirme a importação.");
  } catch (error) {
    setXpStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
});

xpImportButton.addEventListener("click", async () => {
  if (!xpPayload) return;
  xpImportButton.disabled = true;
  try {
    const result = await request("/investments/imports", {
      method: "POST", body: JSON.stringify(xpPayload),
    });
    clearXpPreview();
    await refresh();
    setXpStatus(`${result.imported} lançamentos XP salvos; ${result.skipped} já existiam. ${result.matched_transfers} transferências do Inter confirmadas como investimento.`);
    showNotice("Extrato da XP importado.");
  } catch (error) {
    setXpStatus(error.message, true);
    xpImportButton.disabled = false;
  }
});
