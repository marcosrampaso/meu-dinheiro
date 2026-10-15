const assert = require('node:assert/strict');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const fixture = spawn(process.env.PYTHON_BIN || 'python', ['tests/ui_fixture_server.py', '--origins'], { cwd: path.resolve(__dirname, '..') });
  let logs = '', browser;
  fixture.stderr.on('data', chunk => { logs += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture timeout: ' + logs)), 15000);
      fixture.stdout.once('data', chunk => { clearTimeout(timer); resolve(Number(String(chunk).trim())); });
      fixture.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exit ' + code + logs)); });
    });
    browser = await chromium.launch({ headless:true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath:process.env.BROWSER_EXECUTABLE } : {}) });
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${port}/#financas`);
    await page.locator('#local-auth-button').click(); // Synthetic fixture only.
    await page.waitForFunction(() => document.querySelector('#spending-credit-total').textContent.includes('100,00'));
    assert.match(await page.locator('#spending-account-total').textContent(), /40,00/);
    assert.match(await page.locator('#spending-account-pending').textContent(), /15,00 sem categoria/);
    await page.waitForFunction(() => document.querySelector('#category-month-chart .category-month-row:last-child .category-month-origins')?.textContent.includes('Conta corrente: R$'));
    let origins=page.locator('#category-month-chart .category-month-row:last-child .category-month-origins');
    assert.match(await origins.textContent(),/Cartão: R\$\s*100,00.*Conta corrente: R\$\s*25,00/);
    await page.locator('#chart-months').selectOption('all');
    await page.waitForFunction(() => document.querySelectorAll('#category-month-chart .category-month-row').length === 1);
    await page.locator('[data-spending-type="credit"]').click();
    await page.waitForFunction(() => document.querySelector('#category-title').textContent === 'Cartão de crédito por categoria');
    assert.match(await page.locator('#purchase-total').textContent(), /100,00/);
    await page.waitForFunction(() => document.querySelector('#category-month-chart-title').textContent.includes('Cartão de crédito'));
    assert.equal(await page.locator('#transactions-list .transaction').count(),2);
    assert.equal(await page.locator('#categorization-type').inputValue(),'credit');
    assert.equal(await page.locator('#transactions-list').textContent().then(t => t.includes('Pagamento da fatura')),false);
    await page.locator('[data-spending-type="account"]').click();
    await page.waitForFunction(() => document.querySelector('#category-title').textContent === 'Conta corrente por categoria');
    assert.match(await page.locator('#purchase-total').textContent(),/25,00/);
    assert.equal(await page.locator('#categorization-type').inputValue(),'account');
    assert.equal(await page.locator('#transactions-list').textContent().then(t => t.includes('Mercado no cartão')),false);
    await page.locator('#include-uncategorized').check();
    await page.waitForFunction(() => document.querySelector('#purchase-total').textContent.includes('40,00'));
    await page.waitForFunction(() => document.querySelector('#category-month-chart .category-month-row:last-child .category-month-origins')?.textContent.includes('40,00'));
    origins=page.locator('#category-month-chart .category-month-row:last-child .category-month-origins');
    assert.doesNotMatch(await origins.textContent(),/Cartão/);
    await page.locator('#privacy-toggle').click();
    await page.waitForFunction(() => document.querySelector('#spending-credit-total').textContent === 'R$ ••••');
    assert.equal(await page.locator('#spending-account-total').textContent(),'R$ ••••');
    await page.locator('#privacy-toggle').click();
    await page.reload();
    await page.waitForFunction(() => !document.body.classList.contains('locked') && document.querySelector('#category-title').textContent === 'Conta corrente por categoria');
    assert.equal(await page.locator('#spending-type').inputValue(),'account');
    await page.locator('#scope-view').selectOption('business');
    await page.waitForFunction(() => document.querySelector('#spending-account-total').textContent.includes('10,00'));
    assert.match(await page.locator('#spending-credit-total').textContent(),/0,00/);
    await page.locator('#spending-type').selectOption('all');
    await page.waitForFunction(() => document.querySelector('#category-title').textContent === 'Compras por categoria');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false);
    assert.deepEqual(errors,[]);
    console.log('Payment views: credit/account totals, no double counting, filters, invoice month, classification, privacy, reload, scopes and mobile OK.');
  } catch (error) { console.error(logs.slice(-2500)); throw error; }
  finally { if (browser) await browser.close(); fixture.kill('SIGTERM'); }
}
main().catch(error => {console.error(error); process.exitCode=1;});
