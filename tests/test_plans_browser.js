const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "..");

async function main() {
  const fixture = spawn(process.env.PYTHON_BIN || "python", ["tests/ui_fixture_server.py"], { cwd: root });
  let logs = "";
  fixture.stderr.on("data", chunk => { logs += chunk; });
  let browser, page;
  const errors = [];
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Fixture did not start: " + logs)), 15000);
      fixture.stdout.once("data", chunk => { clearTimeout(timer); resolve(Number(String(chunk).trim())); });
      fixture.once("exit", code => { clearTimeout(timer); reject(new Error("Fixture exited: " + code + logs)); });
    });
    browser = await chromium.launch({ headless: true,
      ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
    page = await browser.newPage({ viewport: { width: 1440, height: 1150 }, locale: "pt-BR" });
    page.on("pageerror", error => errors.push(error.message));
    const base = `http://127.0.0.1:${port}`;
    await page.goto(base + "/#planos");
    // Authentication is mocked only in this isolated fixture process.
    await page.locator("#local-auth-button").click();
    await page.waitForFunction(() => !document.body.classList.contains("locked"));
    await page.waitForFunction(() => document.querySelector('#reserve-plan-form input[name="balance_as_of"]').value !== "");
    assert.equal(await page.locator("#plans-tab").getAttribute("aria-selected"), "true");
    const fields = name => page.locator(`#reserve-plan-form [name="${name}"]`);
    await fields("name").fill("Reserva demonstração");
    await fields("essential_expenses").fill("1200,00");
    await fields("monthly_contribution").fill("400,00");
    await page.locator("#plan-save").click();
    await page.waitForFunction(() => document.querySelector("#plan-save-state").textContent === "Plano salvo");
    assert.match(await page.locator("#plan-target").textContent(), /7\.200,00/);
    assert.equal(await page.locator("#plan-payments").textContent(), "18 aportes");
    await page.reload();
    await page.waitForFunction(() => !document.body.classList.contains("locked"));
    await page.waitForFunction(() => document.querySelector('#reserve-plan-form input[name="monthly_contribution"]').value === "400,00");
    assert.equal(await fields("name").inputValue(), "Reserva demonstração");
    await fields("reserved_balance").fill("600,00");
    await page.locator("#plan-preview").click();
    await page.waitForFunction(() => document.querySelector("#plan-payments").textContent === "17 aportes");
    assert.equal(await page.locator("#plan-save-state").textContent(), "Simulação não salva");
    await page.locator("#plan-save").click();
    await page.waitForFunction(() => document.querySelector("#plan-save-state").textContent === "Plano salvo");
    await page.locator("#privacy-toggle").click();
    assert.equal(await page.locator("#plan-current").textContent(), "R$ ••••");
    assert.equal(await page.locator("#plan-covered").textContent(), "••••");
    assert.equal(await page.locator("#plan-payments").textContent(), "••••");
    assert.equal(await page.locator("#reserve-projection-chart svg").isVisible(), false);
    assert.equal(await page.locator("#plan-progress-fill").isVisible(), false);
    await page.locator("#privacy-toggle").click();
    assert.match(await page.locator("#plan-current").textContent(), /600,00/);
    await fields("monthly_contribution").fill("0,00");
    await page.locator("#plan-preview").click();
    await page.waitForFunction(() => document.querySelector("#plan-progress-state").textContent === "Aportes pausados");
    await fields("monthly_contribution").fill("9000,00");
    await page.locator("#plan-preview").click();
    await page.waitForFunction(() => document.querySelector("#plan-cash-state").classList.contains("error"));
    // Discard the scenario by switching scopes; it must not overwrite the saved plan.
    await page.locator("#plan-scope").selectOption("business");
    await page.waitForFunction(() => document.querySelector("#plan-save-state").textContent === "Novo plano");
    assert.equal(await page.locator("#plan-empty").isVisible(), true);
    await page.locator("#plan-scope").selectOption("personal");
    await page.waitForFunction(() => document.querySelector('#reserve-plan-form input[name="monthly_contribution"]').value === "400,00");
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.evaluate(() => {
      const tabs = document.querySelector(".workspace-tabs");
      return tabs.scrollWidth > tabs.clientWidth;
    }), false);
    if (process.env.UI_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.UI_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, "plan-mobile.png") });
      await page.setViewportSize({ width: 1440, height: 1150 });
      await page.evaluate(() => scrollTo(0, 0));
      await page.waitForTimeout(100);
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, "plan-desktop.png") });
    }
    await page.locator("#plan-delete").click();
    await page.waitForFunction(() => document.querySelector("#plan-save-state").textContent === "Novo plano");
    const unchanged = await page.evaluate(async () => {
      const response = await fetch('/api/v1/transactions?scope=personal', { headers: { 'X-Local-Session': localAuth.token() } });
      return (await response.json()).items.length;
    });
    assert.equal(unchanged, 1);
    assert.deepEqual(errors, []);
    assert.ok(!logs.includes(' 500 '), logs);
    console.log("Plan browser OK: real API, preview, save, reload, update, privacy, pause, cash check, scope isolation, mobile and delete.");
  } catch (error) {
    if (page) console.error({ pageErrors: errors, planStatus: await page.locator("#plan-status").textContent(),
      serverLog: logs.slice(-1800) });
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.kill("SIGTERM");
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
