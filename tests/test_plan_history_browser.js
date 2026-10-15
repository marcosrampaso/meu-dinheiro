const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function main() {
  const fixture = spawn(process.env.PYTHON_BIN || "python", ["tests/ui_fixture_server.py", "--history"], { cwd: path.resolve(__dirname, "..") });
  let logs = "", browser, page;
  const errors = [];
  fixture.stderr.on("data", chunk => { logs += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Fixture timeout: " + logs)), 15000);
      fixture.stdout.once("data", chunk => { clearTimeout(timer); resolve(Number(String(chunk).trim())); });
      fixture.once("exit", code => { clearTimeout(timer); reject(new Error("Fixture exit " + code + logs)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: "pt-BR" });
    page.on("pageerror", error => errors.push(error.message));
    page.on("requestfailed", request => errors.push(request.url() + " " + request.failure()?.errorText));
    await page.goto(`http://127.0.0.1:${port}/#planos`);
    await page.locator("#local-auth-button").click(); // Fake auth in the isolated fixture only.
    await page.waitForFunction(() => document.querySelector("#history-average").textContent.includes("1.680,00"));
    await page.waitForFunction(() => document.querySelector('#reserve-plan-form input[name="balance_as_of"]').value !== "");
    assert.equal(await page.locator("#history-confirmed-income").inputValue(), "");
    assert.equal(await page.locator("#history-apply-base").isDisabled(), true);
    assert.match(await page.locator("#history-base").textContent(), /3 de 6 meses/);
    assert.match(await page.locator("#history-months").textContent(), /de \d+ dias de extrato/);
    assert.match(await page.locator("#history-months").textContent(), /fatura registrada/);
    assert.equal(await page.locator("#history-categories select").count(), 5);
    await page.locator('#history-categories select[data-category="Assinaturas"]').selectOption("essential");
    await page.locator("#history-use-income").click();
    await page.waitForFunction(() => document.querySelector("#history-scenarios").textContent.includes("1.470,00"));
    assert.match(await page.locator("#history-essential").textContent(), /1\.450,00/);
    await page.locator("#history-acknowledged").check();
    await page.locator('[data-scenario="maintain"]').click();
    await page.waitForFunction(() => document.querySelector('#reserve-plan-form input[name="monthly_contribution"]').value === "1470,00");
    assert.equal(await page.locator('#reserve-plan-form input[name="essential_expenses"]').inputValue(), "1450,00");
    const call = route => page.evaluate(async route => {
      const response = await fetch('/api/v1/' + route, { headers: { 'X-Local-Session': localAuth.token() } });
      return response.json();
    }, route);
    assert.equal((await call("plans/reserve")).configured, false);
    assert.equal((await call("plans/history")).preferences.income_cents, 350000);
    const original = (await call("transactions")).items;
    await page.locator("#plan-save").click();
    await page.waitForFunction(() => document.querySelector("#plan-save-state").textContent === "Plano salvo");
    await page.reload();
    await page.waitForFunction(() => !document.body.classList.contains("locked"));
    await page.waitForFunction(() => document.querySelector('#reserve-plan-form input[name="essential_expenses"]').value === "1450,00");
    await page.waitForFunction(() => document.querySelector("#history-confirmed-income").value === "3500,00");
    assert.equal(await page.locator('#history-categories select[data-category="Assinaturas"]').inputValue(), "essential");
    assert.equal(await page.locator("#history-acknowledged").isChecked(), false);
    await page.locator("#privacy-toggle").click();
    assert.equal(await page.locator("#history-average").textContent(), "R$ ••••");
    assert.equal(await page.locator("#history-confirmed-income").getAttribute("type"), "password");
    assert.equal(await page.locator("#history-categories .bar-fill").first().isVisible(), false);
    await page.locator("#privacy-toggle").click();
    await page.locator("#history-confirmed-income").fill("100,00");
    await page.waitForFunction(() => document.querySelector("#history-scenarios").textContent.includes("Faltariam"));
    assert.equal(await page.locator("#history-acknowledged").isChecked(), false);
    await page.locator("#plan-scope").selectOption("business");
    await page.waitForFunction(() => !document.querySelector("#history-empty").hidden);
    assert.equal(await page.locator("#plan-history-form").isVisible(), false);
    await page.locator("#plan-scope").selectOption("personal");
    await page.waitForFunction(() => document.querySelector("#history-confirmed-income").value === "3500,00");
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (process.env.UI_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.UI_SCREENSHOT_DIR, { recursive: true });
      await page.evaluate(() => scrollTo(0,0));
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, "history-mobile.png"), fullPage: true });
      await page.setViewportSize({ width:1440,height:1100 });
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, "history-desktop.png") });
    }
    assert.deepEqual((await call("transactions")).items, original);
    // A category filter explains a broad bucket without changing the stored categories.
    const history = await call("plans/history");
    await page.locator("#finances-tab").click();
    await page.locator("#month").fill(history.base_months.at(-1));
    await page.locator("#month").dispatchEvent("change");
    await page.locator("#transaction-category-filter").selectOption("Outros");
    await page.waitForFunction(() => document.querySelectorAll("#transactions-list .transaction").length === 1);
    assert.match(await page.locator("#transactions-list").textContent(), /Loja a revisar/);
    assert.deepEqual(errors, []);
    assert.ok(!logs.includes(' 500 '), logs);
    console.log("History browser OK: evidence, role review, income confirmation, scenarios, draft vs save, persistence, scope, privacy, mobile and category filter.");
  } catch (error) {
    fs.writeFileSync(path.join(os.tmpdir(), 'plan-history-fixture.log'), logs);
    if (page) console.error({ pageErrors: errors, status: await page.locator("#history-preview-status").textContent(), logs: logs.slice(-1500) });
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.kill("SIGTERM");
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
