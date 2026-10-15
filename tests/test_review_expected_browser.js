const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function main() {
  const fixture = spawn(process.env.PYTHON_BIN || "python", ["tests/ui_fixture_server.py", "--history"], { cwd:path.resolve(__dirname,"..") });
  let browser,page,logs=""; const errors=[];
  fixture.stderr.on("data", chunk=>{ logs+=chunk; });
  try {
    const port=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error("Fixture timeout "+logs)),15000);
      fixture.stdout.once("data",chunk=>{clearTimeout(timer);resolve(Number(String(chunk).trim()));});
      fixture.once("exit",code=>{clearTimeout(timer);reject(new Error("Fixture exit "+code+logs));});
    });
    browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
    page=await browser.newPage({viewport:{width:1440,height:1100},locale:"pt-BR"});
    page.on("pageerror",error=>errors.push(error.message));
    await page.goto(`http://127.0.0.1:${port}/#planos`);
    await page.locator("#local-auth-button").click(); // Biometry is faked only inside the fixture.
    await page.waitForFunction(()=>document.querySelector("#history-average").textContent.includes("1.680,00"));
    await page.locator('#history-recurring-income').locator('..').locator('summary').click();
    await page.getByRole('button',{name:'Planejar esta receita',exact:true}).click();
    const expected = name=>page.locator(`#expected-form [name="${name}"]`);
    assert.equal(await expected('amount').inputValue(),'3500,00');
    await expected('amount').fill('3900,00'); await expected('due_day').fill('31');
    const month=await expected('starts_month').inputValue();
    await expected('ends_month').fill(month);
    await page.locator('#expected-save').click();
    await page.waitForFunction(()=>document.querySelector('#expected-income').textContent.includes('3.900,00'));
    const api=route=>page.evaluate(async route=>{
      const response=await fetch('/api/v1/'+route,{headers:{'X-Local-Session':localAuth.token()}}); return response.json();
    },route);
    assert.equal((await api('summary?month='+month+'&scope=personal')).income_cents,0);
    await page.locator('#expected-use-income').click();
    assert.equal(await page.locator('#history-confirmed-income').inputValue(),'3900,00');
    assert.equal(await page.locator('#history-acknowledged').isChecked(),false);
    await page.locator('#expected-list').getByRole('button',{name:'Editar',exact:true}).click();
    await expected('amount').fill('4000,00'); await page.locator('#expected-save').click();
    await page.waitForFunction(()=>document.querySelector('#expected-income').textContent.includes('4.000,00'));
    await page.locator('#plan-scope').selectOption('business');
    await page.waitForFunction(()=>document.querySelector('#expected-income').textContent.includes('0,00') && document.querySelector('#expected-list').textContent.includes('Cadastre'));
    await page.locator('#plan-scope').selectOption('personal');
    await page.waitForFunction(()=>document.querySelector('#expected-income').textContent.includes('4.000,00'));
    await page.waitForFunction(()=>document.querySelector('#history-categories select[data-category="Outros"]') !== null);
    await page.locator('#history-categories select[data-category="Outros"]').locator('..').locator('..').getByRole('link',{name:'Revisar lançamentos'}).click();
    await page.waitForFunction(()=>document.querySelector('#review-total').textContent==='3');
    assert.equal(await page.locator('#review-tab').getAttribute('aria-selected'),'true');
    const original=(await api('transactions/review?category=Outros')).items[0];
    await page.locator('#review-list select').first().selectOption('Compras');
    assert.equal((await api('transactions/review?category=Outros')).total,3);
    await page.locator('#review-save').click();
    await page.waitForFunction(()=>document.querySelector('#review-total').textContent==='2');
    const changed=await api('transactions/'+original.id);
    for(const field of ['amount_cents','occurred_on','invoice_month','scope','source','excluded','pending']) assert.equal(changed[field],original[field]);
    assert.equal(changed.category,'Compras');
    await page.locator('#review-select-page').click(); await page.locator('#review-bulk-category').selectOption('Mercado');
    await page.locator('#review-fill').click(); assert.match(await page.locator('#review-save').textContent(),/2 alterações/);
    await page.locator('#review-save').click(); await page.waitForFunction(()=>document.querySelector('#review-total').textContent==='0');
    await page.locator('#review-filters [name="category"]').selectOption('');
    await page.locator('#review-filters button[type="submit"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#review-list .review-row').length>3);
    await page.locator('#privacy-toggle').click();
    assert.equal(await page.locator('#review-expenses').textContent(),'R$ ••••');
    assert.equal(await page.locator('#review-monthly .bar-fill').first().isVisible(),false);
    await page.locator('#privacy-toggle').click();
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await page.evaluate(()=>{const t=document.querySelector('.workspace-tabs');return t.scrollWidth>t.clientWidth;}),false);
    if(process.env.UI_SCREENSHOT_DIR){
      fs.mkdirSync(process.env.UI_SCREENSHOT_DIR,{recursive:true}); await page.evaluate(()=>scrollTo(0,0));
      await page.screenshot({path:path.join(process.env.UI_SCREENSHOT_DIR,'review-mobile.png')});
      await page.setViewportSize({width:1440,height:1100}); await page.screenshot({path:path.join(process.env.UI_SCREENSHOT_DIR,'review-desktop.png')});
    }
    assert.deepEqual(errors,[]); assert.ok(!logs.includes(' 500 '),logs);
    console.log('Review/expected browser OK: income evidence, forecasts, edit, scopes, no real income, deep link, category preview, atomic saves, bulk review, privacy and mobile.');
  } catch(error){fs.writeFileSync(path.join(os.tmpdir(),'review-expected-fixture.log'),logs);if(page)console.error({errors,status:await page.locator('#review-status').textContent(),expected:await page.locator('#expected-status').textContent()});throw error;}
  finally{if(browser)await browser.close();fixture.kill('SIGTERM');}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
