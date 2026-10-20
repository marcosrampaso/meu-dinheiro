// Synthetic fixture only. Never opens or authenticates the user's financial server.
const assert = require('node:assert/strict');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const fixture = spawn(process.env.PYTHON_BIN || 'python', ['tests/ui_fixture_server.py', '--weekly'], { cwd: path.resolve(__dirname, '..') });
  let logs = '', browser;
  fixture.stderr.on('data', chunk => { logs += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture timeout: ' + logs)), 15000);
      fixture.stdout.once('data', chunk => { clearTimeout(timer); resolve(Number(String(chunk).trim())); });
      fixture.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exit ' + code + logs)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${port}/#financas`);
    await page.locator('#local-auth-button').click();
    await page.waitForFunction(() => document.querySelector('#weekly-total').textContent.includes('85,00'));
    assert.equal(await page.locator('.weekly-category-bar').count(), 4);
    assert.equal(await page.locator('.weekly-transaction').count(), 4);
    assert.equal(await page.locator('.weekly-day').count(), 2);
    assert.match(await page.locator('#weekly-transactions').textContent(), /Cartão/);
    assert.match(await page.locator('#weekly-transactions').textContent(), /Conta · débito/);
    assert.equal(await page.locator('#weekly-transactions img').count(), 0);
    assert.match(await page.locator('#weekly-transactions').textContent(), /<img src=x/);
    assert.doesNotMatch(await page.locator('#weekly-transactions').textContent(), /Pagamento de fatura|Investimento exemplo|Ignorado exemplo|Receita exemplo|Empresa exemplo/);
    await page.locator('.weekly-category-bar[data-category="Alimentação"]').click();
    assert.equal(await page.locator('.weekly-transaction').count(), 1);
    assert.match(await page.locator('#weekly-list-summary').textContent(), /15,00/);
    assert.equal(await page.locator('.weekly-category-bar[data-category="Alimentação"]').getAttribute('aria-pressed'), 'true');
    await page.locator('#weekly-clear-filter').click();
    assert.equal(await page.locator('.weekly-transaction').count(), 4);
    await page.locator('#privacy-toggle').click();
    await page.waitForFunction(() => document.querySelector('#weekly-categories').inert);
    assert.equal(await page.locator('#weekly-categories').getAttribute('aria-hidden'), 'true');
    assert.doesNotMatch(await page.locator('#weekly-transactions').textContent(), /R\$\s*\d/);
    await page.locator('#privacy-toggle').click();
    await page.waitForFunction(() => !document.querySelector('#weekly-categories').inert);
    await page.locator('#weekly-tab').focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelectorAll('.comparison-total').length === 3);
    assert.equal(await page.locator('#comparison-content').isVisible(), true);
    assert.equal(await page.locator('#weekly-content').isVisible(), false);
    assert.equal(await page.locator('.comparison-chart-row').count(), 3);
    assert.equal(await page.locator('.comparison-table thead th').count(), 4);
    assert.match(await page.locator('#comparison-title').textContent(), /até o dia/);
    await page.locator('.weekly-panel').screenshot({ path: '/private/tmp/financas-comparison-desktop.png' });
    await page.locator('#scope-view').selectOption('business');
    await page.waitForFunction(() => document.querySelector('#comparison-categories').textContent.includes('Serviços'));
    assert.doesNotMatch(await page.locator('#comparison-categories').textContent(), /Assinaturas|Alimentação/);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.locator('.weekly-panel').screenshot({ path: '/private/tmp/financas-comparison-mobile.png' });
    await page.locator('#privacy-toggle').click();
    await page.waitForFunction(() => document.querySelector('#comparison-chart').inert);
    assert.doesNotMatch(await page.locator('#comparison-totals').textContent(), /R\$\s*\d/);
    assert.doesNotMatch(await page.locator('#comparison-categories').textContent(), /R\$\s*\d/);
    await page.locator('#privacy-toggle').click();
    await page.locator('#weekly-tab').click();
    await page.waitForFunction(() => document.querySelector('#weekly-total').textContent.includes('500,00'));
    assert.equal(await page.locator('.weekly-transaction').count(), 1);
    await page.locator('#scope-view').selectOption('all');
    await page.waitForFunction(() => document.querySelector('#weekly-total').textContent.includes('585,00'));
    assert.match(await page.locator('#weekly-transactions').textContent(), /PJ/);
    assert.match(await page.locator('#weekly-transactions').textContent(), /Pessoal/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.locator('.weekly-panel').screenshot({ path: '/private/tmp/financas-weekly-mobile.png' });
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.locator('#scope-view').selectOption('personal');
    await page.waitForFunction(() => document.querySelector('#weekly-total').textContent.includes('85,00'));
    await page.locator('.weekly-panel').screenshot({ path: '/private/tmp/financas-weekly-desktop.png' });
    assert.deepEqual(errors, []);
    console.log('Weekly charts, category drill-down, daily list, safe descriptions, scope, month comparison, keyboard, privacy and mobile OK.');
  } catch (error) { console.error(logs.slice(-1500)); throw error; }
  finally { if (browser) await browser.close(); fixture.kill('SIGTERM'); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
