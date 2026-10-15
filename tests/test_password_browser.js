const assert = require('node:assert/strict');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const fixture = spawn(process.env.PYTHON_BIN || 'python', ['tests/ui_fixture_server.py', '--password-auth'], { cwd: path.resolve(__dirname, '..') });
  let logs = '', browser, page;
  const errors = [];
  fixture.stderr.on('data', chunk => { logs += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture timeout: ' + logs)), 15000);
      fixture.stdout.once('data', chunk => { clearTimeout(timer); resolve(Number(String(chunk).trim())); });
      fixture.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exited: ' + code + logs)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
    page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${port}/#configuracoes`);
    assert.equal(await page.locator('#local-password-login').isVisible(), false);
    // Native biometrics are simulated ONLY in this temporary synthetic fixture.
    await page.locator('#local-auth-button').click();
    await page.waitForFunction(() => !document.body.classList.contains('locked'));
    const fields = name => page.locator(`#local-password-settings [name="${name}"]`);
    await fields('password').fill('Senha ficticia para testar!');
    await fields('password_confirmation').fill('Outra senha ficticia!');
    await page.locator('#local-password-save').click();
    assert.match(await page.locator('#local-password-settings-status').textContent(), /iguais/);
    await fields('password_confirmation').fill('Senha ficticia para testar!');
    await page.locator('#local-password-save').click();
    await page.waitForFunction(() => document.querySelector('#local-password-state').textContent === 'Senha cadastrada');
    assert.equal(await fields('password').inputValue(), '');
    await page.locator('#local-lock-button').click();
    await page.locator('#local-password-login').waitFor({ state: 'visible' });
    await page.locator('#local-password-login [name="password"]').fill('wrong');
    await page.locator('#local-password-login-button').click();
    await page.waitForFunction(() => document.querySelector('#local-auth-status').textContent.includes('incorreta'));
    assert.equal(await page.evaluate(() => document.body.classList.contains('locked')), true);
    await page.locator('#local-password-login [name="password"]').fill('Senha ficticia para testar!');
    await page.locator('#local-password-login-button').click();
    await page.waitForFunction(() => !document.body.classList.contains('locked'));
    await page.waitForFunction(() => !document.querySelector('#local-password-settings [name="current_password"]').closest('label').hidden);
    const session = await page.evaluate(async () => {
      const response = await fetch('/api/v1/local-auth/session', { headers: { 'X-Local-Session': localAuth.token() } });
      return response.json();
    });
    assert.equal(session.method, 'password');
    assert.ok(session.principal_id);
    await fields('password').fill('Segunda senha ficticia 42!');
    await fields('password_confirmation').fill('Segunda senha ficticia 42!');
    await fields('current_password').fill('wrong');
    await page.locator('#local-password-save').click();
    await page.waitForFunction(() => document.querySelector('#local-password-settings-status').textContent.includes('incorreta'));
    await fields('password').fill('Segunda senha ficticia 42!');
    await fields('password_confirmation').fill('Segunda senha ficticia 42!');
    await fields('current_password').fill('Senha ficticia para testar!');
    await page.locator('#local-password-save').click();
    await page.waitForFunction(() => document.querySelector('#local-password-settings-status').textContent.includes('Senha local salva'));
    assert.equal(await fields('current_password').inputValue(), '');
    // Minimizing/changing tab preserves the existing five-minute session.
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    assert.equal(await page.evaluate(() => document.body.classList.contains('locked')), false);
    await page.reload();
    await page.waitForFunction(() => !document.body.classList.contains('locked'));
    assert.ok(await page.evaluate(() => localAuth.token()));
    await page.locator('#local-lock-button').click();
    await page.locator('#local-password-login').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => localAuth.token()), null);
    await page.locator('#local-password-login [name="password"]').fill('Segunda senha ficticia 42!');
    await page.locator('#local-password-login-button').click();
    await page.waitForFunction(() => !document.body.classList.contains('locked'));
    await fields('current_password').fill('Segunda senha ficticia 42!');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#local-password-remove').click();
    await page.waitForFunction(() => document.querySelector('#local-password-state').textContent === 'Somente Touch ID');
    await page.locator('#local-lock-button').click();
    await page.locator('#local-auth-button').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#local-password-login').isVisible(), false);
    assert.deepEqual(errors, []);
    console.log('Password UI: initial setup, login, rejection, change, revoke, reload and removal OK (synthetic data only).');
  } catch (error) {
    console.error('Password fixture diagnostics:', logs.slice(-2000), errors);
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.kill('SIGTERM');
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
