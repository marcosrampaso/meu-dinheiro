const positionForm = document.querySelector("#xp-position-form");
const positionFile = positionForm.elements.file;
const positionStatus = document.querySelector("#xp-position-status");
const positionPreview = document.querySelector("#xp-position-preview");
const positionButton = document.querySelector("#xp-position-button");
let positionPayload = null;

function clearPositionPreview() {
  positionPayload = null;
  positionButton.disabled = true;
  positionPreview.hidden = true;
  positionPreview.replaceChildren();
}

function setPositionStatus(message, error = false) {
  positionStatus.textContent = message;
  positionStatus.classList.toggle("error", error);
}

positionFile.addEventListener("change", () => { clearPositionPreview(); setPositionStatus(""); });

positionForm.addEventListener("submit", async event => {
  event.preventDefault();
  clearPositionPreview();
  const file = positionFile.files[0];
  if (!file) return;
  if (file.size > 2_000_000 || !file.name.toLowerCase().endsWith(".xlsx")) {
    setPositionStatus("Selecione a posição XLSX da XP com até 2 MB.", true);
    return;
  }
  const submit = positionForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Não foi possível ler a planilha."));
      reader.readAsDataURL(file);
    });
    const payload = { filename: file.name, content_base64: String(dataUrl).split(",", 2)[1] };
    const result = await request("/investments/positions/preview", {
      method: "POST", body: JSON.stringify(payload),
    });
    const title = document.createElement("strong");
    title.textContent = `Posição de ${formatDate(result.as_of)} · ${result.assets.length} ativos · Patrimônio ${money.format(result.total_cents / 100)}${result.replaces_existing ? " · Substitui a posição salva nessa data" : ""}`;
    positionPreview.append(title);
    for (const asset of result.assets) {
      const row = document.createElement("div");
      row.className = "preview-row";
      const name = document.createElement("span");
      name.textContent = asset.name;
      const value = document.createElement("b");
      value.textContent = money.format(asset.value_cents / 100);
      row.append(name, value);
      positionPreview.append(row);
    }
    if (result.receivables_cents) {
      const note = document.createElement("p");
      note.textContent = `Inclui ${money.format(result.receivables_cents / 100)} de proventos a receber no patrimônio total.`;
      positionPreview.append(note);
    }
    positionPreview.hidden = false;
    positionPayload = payload;
    positionButton.disabled = false;
    setPositionStatus("Confira os valores e confirme a importação.");
  } catch (error) {
    setPositionStatus(error.message, true);
  } finally {
    submit.disabled = false;
  }
});

positionButton.addEventListener("click", async () => {
  if (!positionPayload) return;
  positionButton.disabled = true;
  try {
    const result = await request("/investments/positions", {
      method: "POST", body: JSON.stringify(positionPayload),
    });
    clearPositionPreview();
    document.dispatchEvent(new Event("investment-data-change"));
    setPositionStatus(`Posição de ${formatDate(result.as_of)} salva: ${result.asset_count} ativos, ${money.format(result.total_cents / 100)}.`);
    showNotice("Posição da XP importada.");
  } catch (error) {
    setPositionStatus(error.message, true);
    positionButton.disabled = false;
  }
});
