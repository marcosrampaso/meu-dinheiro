const assert = require('node:assert/strict');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const fixture = spawn(process.env.PYTHON_BIN || 'python', ['tests/ui_fixture_server.py', '--idle-auth'], { cwd: path.resolve(__dirname, '..') });
  let logs = '', browser;
  fixture.stderr.on('data', chunk => { logs += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture timeout: ' + logs)), 15000);
      fixture.stdout.once('data', chunk => { clearTimeout(timer); resolve(Number(String(chunk).trim())); });
      fixture.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exited: ' + code + logs)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
    const page = await browser.newPage();
    const errors = [];
    let unlocks = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => { if (req.url().endsWith('/local-auth/unlock')) unlocks++; });
    const now = new Date('2026-10-15T12:00:00Z');
    await page.clock.install({ time: now });
    await page.clock.pauseAt(now);
    await page.goto(`http://127.0.0.1:${port}/#financas`);
    await page.locator('#local-auth-button').click(); // Synthetic fixture only; no native biometrics.
    await page.waitForFunction(() => !document.body.classList.contains('locked'));
    const input = page.locator('#transaction-form [name="description"]');
    await input.fill('Rascunho preservado');
    const advance = async seconds => {
      await page.request.post(`http://127.0.0.1:${port}/test/advance`, { data: { seconds } });
      await page.clock.runFor(seconds * 1000);
    };
    const active = async () => {
      const response = page.waitForResponse(r => r.url().endsWith('/local-auth/activity') && r.request().method() === 'POST');
      await input.press('End');
      await page.clock.runFor(1);
      assert.equal((await response).status(), 200);
    };
    // Real input keeps the session open beyond five minutes since initial login.
    for (let i = 0; i < 3; i++) { await advance(120); await active(); }
    assert.equal(await page.evaluate(() => document.body.classList.contains('locked')), false);
    await page.reload();
    await page.waitForFunction(() => !document.body.classList.contains('locked'));
    assert.equal(unlocks, 1, 'F5 must restore without a new biometric prompt');
    await input.evaluate(el => { el.value = 'Continuar este rascunho'; });
    const token = await page.evaluate(() => localAuth.token());
    const deadline = await page.evaluate(() => JSON.parse(sessionStorage.getItem('local-idle-session-v1')).expires_at);
    await page.evaluate(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'A' })));
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('local-idle-session-v1')).expires_at), deadline, 'Synthetic events must not renew');
    await advance(299);
    assert.equal(await page.evaluate(() => document.body.classList.contains('locked')), false);
    // Reload and background reads must not reset the idle deadline.
    await page.reload();
    await page.waitForFunction(() => !document.body.classList.contains('locked'));
    // Set a draft without generating user activity to verify locking preserves the DOM.
    await input.evaluate(el => { el.value = 'Rascunho antes do bloqueio'; });
    await advance(1);
    assert.equal(await page.evaluate(() => document.body.classList.contains('locked')), true);
    assert.equal(await page.evaluate(() => localAuth.token()), null);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('local-idle-session-v1')), null);
    assert.equal(await input.inputValue(), 'Rascunho antes do bloqueio');
    const headers = { 'X-Local-Session': token };
    assert.equal((await page.request.get(`http://127.0.0.1:${port}/api/v1/local-auth/session`, { headers })).status(), 401);
    assert.equal((await page.request.post(`http://127.0.0.1:${port}/api/v1/local-auth/activity`, { headers, data: {} })).status(), 401);
    await page.locator('#local-auth-button').click();
    await page.waitForFunction(() => !document.body.classList.contains('locked'));
    assert.equal(await input.inputValue(), 'Rascunho antes do bloqueio');
    await page.locator('#local-lock-button').click();
    await page.waitForFunction(() => document.body.classList.contains('locked'));
    await page.reload();
    await page.locator('#local-auth-button:not([disabled])').waitFor();
    assert.equal(await page.evaluate(() => document.body.classList.contains('locked')), true);
    assert.deepEqual(errors, []);
    console.log('Idle session: activity renewal, F5 restoration, no renewal from polls/reload/synthetic events, expiry, drafts and manual lock OK.');
  } catch (error) { console.error(logs.slice(-2500)); throw error; }
  finally { if (browser) await browser.close(); fixture.kill('SIGTERM'); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
