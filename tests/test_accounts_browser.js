const assert=require('node:assert/strict');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
async function main(){
 const fixture=spawn(process.env.PYTHON_BIN || 'python',['tests/account_fixture_server.py'],{cwd:path.resolve(__dirname,'..')});
 let logs='',browser;fixture.stderr.on('data',chunk=>{logs+=chunk;});
 try {
  const port=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(logs)),15000);fixture.stdout.once('data',chunk=>{clearTimeout(timer);resolve(Number(String(chunk).trim()));});fixture.once('exit',code=>reject(new Error('fixture '+code+logs)));});
  browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.locator('#local-register-toggle').waitFor({state:'visible'});
  await page.locator('#local-register-toggle').click();
  async function fillUser(name){await page.locator('#local-register-form [name="display_name"]').fill(name);await page.locator('#local-register-form [name="username"]').fill(name);for(const field of ['password','password_confirmation'])await page.locator(`#local-register-form [name="${field}"]`).fill('synthetic strong password');}
  await fillUser('alice');await page.locator('#local-register-button').click();
  await page.waitForFunction(()=>!document.body.classList.contains('locked'));
  await page.waitForFunction(()=>document.querySelector('#local-account-label').textContent.includes('@alice'));
  const firstToken=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('local-idle-session-v1')).token);
  const created=await page.request.post(`http://127.0.0.1:${port}/api/v1/transactions`,{headers:{'X-Local-Session':firstToken},data:{kind:'expense',description:'ALICE PRIVATE ROW',category:'Mercado',amount:'123',occurred_on:new Date().toISOString().slice(0,10)}});
  assert.equal(created.status(),201);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#transactions-list').textContent.includes('ALICE PRIVATE ROW'));
  await page.locator('#local-account-switch').click();await page.locator('#local-register-toggle').waitFor({state:'visible'});
  await page.locator('#local-register-toggle').click();await fillUser('bruno');await page.locator('#local-register-button').click();
  await page.waitForFunction(()=>!document.body.classList.contains('locked') && document.querySelector('#local-account-label').textContent.includes('@bruno'));
  assert.equal(await page.locator('#transactions-list').textContent().then(t=>t.includes('ALICE PRIVATE ROW')),false);
  await page.locator('#local-account-switch').click();await page.locator('#local-password-login').waitFor({state:'visible'});
  await page.locator('#local-password-login [name="username"]').fill('alice');await page.locator('#local-password-login [name="password"]').fill('synthetic strong password');await page.locator('#local-password-login-button').click();
  await page.waitForFunction(()=>!document.body.classList.contains('locked') && document.querySelector('#transactions-list').textContent.includes('ALICE PRIVATE ROW'));
  await page.locator('#local-account-switch').click();await page.locator('#local-demo-button').waitFor({state:'visible'});await page.locator('#local-demo-button').click();
  await page.waitForFunction(()=>!document.body.classList.contains('locked') && document.querySelector('#local-account-banner').classList.contains('demo-mode'));
  assert.equal(await page.locator('#transactions-list').textContent().then(t=>t.includes('ALICE PRIVATE ROW')),false);
  await page.locator('#news-tab').click();await page.locator('.news-card').first().waitFor();
  assert.match(await page.locator('#news-report').textContent(),/fictício/);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator('#local-account-switch').click();await page.locator('#local-register-toggle').waitFor({state:'visible'});await page.locator('#local-register-toggle').click();
  await page.screenshot({path:'/private/tmp/financas-accounts-mobile.png'});
  assert.deepEqual(errors,[]);console.log('Local accounts: first registration, password login, F5, user switch, isolated data, demo and mobile OK.');
 }catch(e){console.error(logs.slice(-3000));throw e;}finally{if(browser)await browser.close();fixture.kill('SIGTERM');}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
