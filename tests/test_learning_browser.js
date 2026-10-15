const assert = require('node:assert/strict');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const fixture = spawn(process.env.PYTHON_BIN || 'python', ['tests/ui_fixture_server.py', '--learning'], { cwd: path.resolve(__dirname, '..') });
  let logs = '', browser;
  fixture.stderr.on('data', chunk => { logs += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture timeout: ' + logs)), 15000);
      fixture.stdout.once('data', chunk => { clearTimeout(timer); resolve(Number(String(chunk).trim())); });
      fixture.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exit ' + code + logs)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${port}/#financas`);
    await page.locator('#local-auth-button').click(); // Synthetic fixture only.
    await page.waitForFunction(() => !document.querySelector('#suggest-categories').disabled);
    assert.equal(await page.locator('#categorization-engine').inputValue(), 'local');
    await page.locator('#suggest-categories').click();
    await page.locator('#categorization-preview').waitFor({ state: 'visible' });
    const rows = page.locator('#categorization-list .ai-category-row');
    const low = rows.filter({ hasText: '32,00' });
    const high = rows.filter({ hasText: '160,00' });
    const uncertain = rows.filter({ hasText: '80,00' });
    assert.equal(await low.locator('select').inputValue(), 'Tabaco');
    assert.equal(await high.locator('select').inputValue(), 'Transporte');
    assert.match(await low.textContent(), /kNN local/);
    assert.equal(await uncertain.locator('input').isChecked(), false);
    assert.equal(await uncertain.locator('select').inputValue(), '');
    await uncertain.locator('select').selectOption('Tabaco');
    assert.equal(await uncertain.locator('input').isChecked(), true);
    await page.locator('#apply-categories').click();
    await page.waitForFunction(() => document.querySelector('#categorization-preview').hidden);
    await page.locator('#categorization-engine').selectOption('hybrid');
    await page.waitForFunction(() => document.querySelector('#categorization-status').textContent.includes('Conecte o ChatGPT'));
    assert.equal(await page.locator('#suggest-categories').isDisabled(), true);
    // Test the UI history request contract without accessing real credentials or banks.
    await page.route('**/api/v1/investments/pluggy/status', route => route.fulfill({ json: { configured:false,inter_configured:true,credentials_saved:true } }));
    let requested;
    await page.route('**/api/v1/accounts/pluggy/inter/preview', async route => {
      requested = route.request().postDataJSON();
      await route.fulfill({ json: { preview_token:'fixture-preview',full_history:requested.full_history,
        new_count:1,replaced_count:0,accounts:[],periods:[{source:'Inter · conta corrente',starts_on:'2026-05-01',ends_on:'2026-10-14'}],
        skipped:{},new_items:[],note:'Prévia de teste local.' } });
    });
    await page.locator('a[href="#configuracoes"]').first().click();
    await page.waitForFunction(() => !document.querySelector('#pluggy-inter-preview-button').disabled);
    await page.locator('#pluggy-inter-full-history').check();
    await page.locator('#pluggy-inter-preview-button').click();
    await page.waitForFunction(() => !document.querySelector('#pluggy-inter-import-button').disabled);
    assert.equal(requested.full_history, true);
    assert.match(await page.locator('#pluggy-inter-status').textContent(), /período completo/);
    await page.locator('#pluggy-inter-full-history').uncheck();
    assert.equal(await page.locator('#pluggy-inter-import-button').isDisabled(), true);
    assert.equal(await page.locator('#pluggy-inter-preview').isVisible(), false);
    assert.deepEqual(errors, []);
    console.log('Learning/history UI: local kNN without ChatGPT, abstention, manual teaching, apply, history request and stale-preview clearing OK.');
  } catch (error) { console.error(logs.slice(-2000)); throw error; }
  finally { if (browser) await browser.close(); fixture.kill('SIGTERM'); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
