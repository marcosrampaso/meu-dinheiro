// Serve a static fixture with synthetic figures. No financial API, credentials
// or real Touch ID session is used by this browser test.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "..");

let fixture = fs.readFileSync(path.join(root, "static/index.html"), "utf8")
  .replace(/<script[^>]*src="[^"]+"[^>]*><\/script>/g, "")
  .replace('class="locked"', 'class=""')
  .replace('id="local-auth-overlay"', 'id="local-auth-overlay" hidden')
  .replace("</head>", '<script src="/privacy.js" defer></script></head>');

const server = http.createServer((request, response) => {
  const name = request.url.split("?")[0];
  if (name === "/") { response.setHeader("Content-Type", "text/html; charset=utf-8"); response.end(fixture); return; }
  if (!["/privacy.js", "/styles.css", "/dashboard.css"].includes(name)) { response.writeHead(404); response.end(); return; }
  response.setHeader("Content-Type", name.endsWith(".js") ? "text/javascript" : "text/css");
  response.end(fs.readFileSync(path.join(root, "static", name.slice(1))));
});

async function seed(page) {
  await page.evaluate(() => {
    for (const [id, text] of Object.entries({ balance: "R$ 2.480,00", income: "R$ 6.250,00",
      expenses: "R$ 3.770,00", "card-payment": "R$ 1.240,00",
      "card-purchases": "Compras registradas: R$ 980,00", "purchase-total": "R$ 980,00" })) {
      document.getElementById(id).textContent = text;
    }
    document.querySelector("#portfolio-result-amount").textContent = "+R$ 24,50";
    document.querySelector("#portfolio-result-percent").textContent = "+1,25%";
    document.querySelector("#portfolio-result-amount").classList.add("positive");
    document.querySelector("#portfolio-result-percent").classList.add("positive");
    document.querySelector("#portfolio-result-period").textContent = "Histórico de 01/01/2026 a 31/01/2026";
    document.querySelector("#portfolio-result-source").textContent = "Automático · Pluggy";
    document.querySelector("#portfolio-result-note").textContent = "Dados de demonstração. Resultado com aportes e saques ajustados.";
    document.querySelector("#month").value = "2026-01";
    document.querySelector("#health-badge").textContent = "Dados conferidos";
    document.querySelector("#health-items").innerHTML = '<div class="health-item"><strong>Conta pessoal</strong><p>Extrato atualizado.</p></div><div class="health-item"><strong>Cartão de crédito</strong><p>Compras disponíveis.</p></div>';
    document.querySelector("#cashflow-note").textContent = "Dados de demonstração. Sem transferências internas.";
    document.querySelector("#invoice-chart-note").textContent = "Dados de demonstração.";
    for (const id of ["cashflow-chart", "invoice-chart"]) {
      document.getElementById(id).innerHTML = '<svg viewBox="0 0 640 260"><path d="M60 55h540M60 110h540M60 165h540M60 220h540" stroke="#edf0f6" stroke-dasharray="4 5"/><text x="8" y="59" class="chart-axis-text">R$ 8 mil</text><text x="8" y="114" class="chart-axis-text">R$ 6 mil</text><text x="8" y="169" class="chart-axis-text">R$ 4 mil</text><text x="8" y="224" class="chart-axis-text">R$ 2 mil</text><g fill="#8f7df2"><rect x="120" y="100" width="32" height="120" rx="6"/><rect x="220" y="80" width="32" height="140" rx="6"/><rect x="320" y="55" width="32" height="165" rx="6"/><rect x="420" y="105" width="32" height="115" rx="6"/><rect x="520" y="85" width="32" height="135" rx="6"/></g><g fill="#27be98"><rect x="155" y="140" width="32" height="80" rx="6"/><rect x="255" y="120" width="32" height="100" rx="6"/><rect x="355" y="160" width="32" height="60" rx="6"/><rect x="455" y="135" width="32" height="85" rx="6"/><rect x="555" y="150" width="32" height="70" rx="6"/></g></svg>';
    }
    const row = document.createElement("div");
    row.id = "fixture-sensitive";
    row.textContent = "Referência: −R$ 125,70 · retorno +3,25% · 14 cotas · 02/01/2026";
    row.title = "Saldo R$ 2.480,00";
    row.setAttribute("aria-label", "Rendimento +3,25% sobre R$ 100,00");
    document.querySelector("#transactions-list").append(row);
    document.querySelector('#transaction-form input[name="amount"]').value = "125,70";
  });
}

async function main() {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({ headless: true,
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1150 }, locale: "pt-BR" });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(base);
    await seed(page);
    const amount = page.locator('#transaction-form input[name="amount"]');
    await page.locator("#privacy-toggle").click();
    await page.waitForFunction(() => document.querySelector("#balance").textContent === "R$ ••••");
    assert.match(await page.locator("#fixture-sensitive").textContent(), /retorno ••••% · •••• cotas · 02\/01\/2026/);
    assert.equal(await page.locator("#fixture-sensitive").getAttribute("title"), "Saldo R$ ••••");
    assert.equal(await page.locator("#fixture-sensitive").getAttribute("aria-label"), "Rendimento ••••% sobre R$ ••••");
    assert.equal(await amount.getAttribute("type"), "password");
    assert.equal(await amount.inputValue(), "125,70");
    assert.equal(await page.locator("#cashflow-chart").getAttribute("aria-hidden"), "true");
    assert.equal(await page.locator("#cashflow-chart svg").isVisible(), false);
    assert.equal(await page.locator("#privacy-toggle .eye-open").isVisible(), false);
    assert.equal(await page.locator("#privacy-toggle .eye-closed").isVisible(), true);
    assert.equal(await page.locator("#portfolio-result-amount").textContent(), "R$ ••••");
    assert.equal(await page.locator("#portfolio-result-percent").textContent(), "••••%");
    // An incoming API render must remain masked and restore the latest value.
    await page.evaluate(() => {
      document.querySelector("#balance").textContent = "R$ 3.210,50";
      document.querySelector("#fixture-sensitive").title = "Atualizado R$ 3.210,50";
      const dynamic = document.createElement("p");
      dynamic.id = "fixture-dynamic";
      dynamic.textContent = "Novo valor: R$ 55,90";
      document.querySelector("#weekly-report").append(dynamic);
    });
    await page.waitForFunction(() => document.querySelector("#fixture-dynamic").textContent === "Novo valor: R$ ••••");
    page.once("dialog", async dialog => {
      assert.equal(dialog.message(), "Confirmar R$ ••••?");
      await dialog.dismiss();
    });
    await page.evaluate(() => window.confirm("Confirmar R$ 125,70?"));
    await page.locator("#privacy-toggle").click();
    assert.equal(await page.locator("#balance").textContent(), "R$ 3.210,50");
    assert.equal(await page.locator("#fixture-sensitive").getAttribute("title"), "Atualizado R$ 3.210,50");
    assert.equal(await page.locator("#fixture-dynamic").textContent(), "Novo valor: R$ 55,90");
    await page.locator("#privacy-toggle").click();
    // Preference is restored before new data is rendered after reload.
    await page.reload();
    await seed(page);
    await page.waitForFunction(() => document.querySelector("#balance").textContent === "R$ ••••");
    assert.equal(await page.locator("#privacy-toggle").getAttribute("aria-pressed"), "true");
    if (process.env.UI_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.UI_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, "financas-hidden.png") });
    }
    await page.locator("#privacy-toggle").click();
    assert.equal(await page.locator("#balance").textContent(), "R$ 2.480,00");
    assert.equal(await amount.getAttribute("type"), null);
    assert.equal(await amount.inputValue(), "125,70");
    assert.equal(await page.locator("#cashflow-chart").getAttribute("aria-hidden"), null);
    assert.equal(await page.locator("#cashflow-chart svg").isVisible(), true);
    assert.equal(await page.locator("#privacy-toggle .eye-open").isVisible(), true);
    assert.equal(await page.locator("#privacy-toggle .eye-closed").isVisible(), false);
    assert.equal(await page.locator("#portfolio-result-amount").textContent(), "+R$ 24,50");
    assert.equal(await page.locator("#portfolio-result-percent").textContent(), "+1,25%");
    if (process.env.UI_SCREENSHOT_DIR) {
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, "financas-desktop.png") });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    assert.equal(await page.locator("#privacy-toggle").isVisible(), true);
    assert.equal(await page.evaluate(() => {
      const tabs = document.querySelector(".workspace-tabs");
      return tabs.scrollWidth > tabs.clientWidth;
    }), false);
    if (process.env.UI_SCREENSHOT_DIR) {
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, "financas-mobile.png") });
    }
    await page.evaluate(() => {
      document.querySelector("#finances-view").hidden = true;
      document.querySelector("#investments-view").hidden = false;
      document.querySelector("#finances-tab").setAttribute("aria-selected", "false");
      document.querySelector("#investments-tab").setAttribute("aria-selected", "true");
      window.scrollTo(0, 0);
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    assert.equal(await page.locator("#portfolio-result-percent").textContent(), "+1,25%");
    if (process.env.UI_SCREENSHOT_DIR) {
      await page.setViewportSize({ width: 1440, height: 1150 });
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, "investimentos-summary.png") });
    }
    assert.deepEqual(errors, []);
    console.log("Privacy browser OK: amounts, percentages, quantities, tooltips, inputs, charts, live updates, reload and mobile.");
  } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
