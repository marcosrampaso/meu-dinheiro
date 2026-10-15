const assert = require('node:assert/strict');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const fixture=spawn(process.env.PYTHON_BIN || 'python',['tests/ui_fixture_server.py','--news'],{cwd:path.resolve(__dirname,'..')});
  let logs='',browser;
  fixture.stderr.on('data',chunk=>{logs+=chunk;});
  try {
    const port=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Fixture timeout '+logs)),15000);
      fixture.stdout.once('data',chunk=>{clearTimeout(timer);resolve(Number(String(chunk).trim()));});
      fixture.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exit '+code+logs));});
    });
    browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    const page=await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[],posts=[];
    let cached=null;
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/v1/investments/news**',async route=>{
      const req=route.request();
      if (req.method()==='GET') {
        const response=await route.fetch();const data=await response.json();
        await route.fulfill({json:{...data,chatgpt_connected:true,report:cached?.uses_finances === (new URL(req.url()).searchParams.get('use_finances') === 'true') ? cached : null,report_stale:false}});return;
      }
      const options=req.postDataJSON();posts.push(options);
      const response=await page.request.get(`http://127.0.0.1:${port}/api/v1/investments/news?use_finances=${options.use_finances}`,{headers:{'X-Local-Session':'synthetic-ui-session'}});
      const data=await response.json();
      cached={generated_at:new Date().toISOString(),model:options.model,uses_finances:options.use_finances,financial_context:data.financial_context,
        assets:['ABCD11','EFGH11'].map((asset,index)=>({asset,headline:index ? 'Gestor publica novo relatório de resultados' : 'Resultado do trimestre mostra avanço da receita',
          summary:'Documento de exemplo para testar o jornal.',source_url:`https://www.b3.com.br/noticias/${asset}`,published_on:'2026-10-10',source_kind:'relatorio',
          what_changed:'A receita cresceu no período, segundo o relatório de exemplo.',why_it_matters:'A evolução da receita pode afetar a geração de caixa; isso não garante valorização.',
          watch_next:'Compare a margem operacional no próximo relatório com a margem deste trimestre.',short_term:'Observar o próximo resultado.',medium_term:'Conferir a execução dos projetos.',long_term:'Reavaliar a tese se a margem cair.',risk:'Oscilação das receitas.'})).concat([{asset:'ZZZZ11',source_url:''}]),
        opportunities:[{title:'Compare custos e prazo de resgate',thesis:'Avalie taxa e liquidez antes de escolher um produto.',risk:'Taxas e condições podem mudar.',source_url:'https://www.gov.br/investidor/'}],
        personalization:options.use_finances ? {summary:'Confira a reserva e as obrigações antes de ampliar os aportes.',priorities:[{title:'Conferir o dinheiro disponível',action:'Confirme as despesas dos próximos dias antes de aportar.',reason:'O saldo de R$ 6.000,00 não é sobra garantida para investir.',benefit:'Reduzir a chance de precisar resgatar às pressas.',condition:'Confira a fatura e os compromissos.',risk:'O histórico pode estar parcial.',basis:'cash',source_url:''}]} : null};
      await route.fulfill({json:{...data,report:cached,chatgpt_connected:true,report_stale:false}});
    });
    await page.goto(`http://127.0.0.1:${port}/#noticias`);
    await page.locator('#local-auth-button').click();
    await page.locator('.news-metric').first().waitFor();
    await page.waitForFunction(()=>!document.querySelector('#news-refresh').disabled);
    assert.equal(await page.locator('#news-model').inputValue(),'gpt-6.1-sol');
    assert.equal(await page.locator('#news-use-finances').isChecked(),true);
    assert.equal(await page.locator('.news-metric').count(),4);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:'/private/tmp/financas-news-hero.png'});
    await page.locator('#news-refresh').click();
    await page.locator('.news-action-card').waitFor();
    assert.deepEqual(posts[0],{model:'gpt-6.1-sol',use_finances:true});
    assert.equal(await page.locator('.news-card').count(),2);
    assert.match(await page.locator('.news-empty-note').textContent(),/ZZZZ11/);
    assert.match(await page.locator('.news-card').first().textContent(),/margem operacional/);
    assert.equal(await page.locator('.news-card a').first().getAttribute('rel'),'noopener noreferrer');
    await page.locator('.news-filter',{hasText:'ABCD11'}).click();
    assert.equal(await page.locator('.news-card:visible').count(),1);
    await page.locator('.news-filter',{hasText:'Todos'}).click();
    await page.locator('.news-card .news-scenarios').first().locator('summary').click();
    assert.match(await page.locator('.news-card .news-scenarios').first().textContent(),/0–3 meses/);
    await page.screenshot({path:'/private/tmp/financas-news-desktop.png'});
    await page.locator('.news-filters').scrollIntoViewIfNeeded();
    await page.screenshot({path:'/private/tmp/financas-news-cards.png'});
    await page.locator('#privacy-toggle').click();
    await page.waitForFunction(()=>document.querySelector('.news-metric strong').textContent.includes('••••'));
    assert.equal(await page.locator('.news-action-lead').textContent(),'••••');
    assert.equal(await page.locator('.news-action-card p').allTextContents().then(rows=>rows.some(text=>text.includes('6.000'))),false);
    await page.locator('#privacy-toggle').click();
    await page.locator('#news-use-finances').uncheck();
    await page.waitForFunction(()=>!document.querySelector('#news-context-preview').textContent);
    assert.equal(await page.locator('.news-action-card').count(),0);
    await page.locator('#news-model').selectOption('gpt-6-luna');
    await page.locator('#news-refresh').click();
    await page.waitForFunction(()=>document.querySelector('.news-report-stamp')?.textContent.includes('somente ativos'));
    assert.deepEqual(posts[1],{model:'gpt-6-luna',use_finances:false});
    assert.equal(await page.locator('.news-action-card').count(),0);
    await page.setViewportSize({width:390,height:844});
    await page.locator('.news-filters').scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:'/private/tmp/financas-news-mobile.png'});
    await page.reload();
    await page.waitForFunction(()=>!document.body.classList.contains('locked'));
    assert.equal(await page.locator('#news-use-finances').isChecked(),false);
    assert.equal(await page.locator('#news-model').inputValue(),'gpt-6-luna');
    assert.deepEqual(errors,[]);
    console.log('News journal: cards, source links, scenarios, filters, financial context, model choice, consent, privacy and mobile OK.');
  } catch(error) {console.error(logs.slice(-2000));throw error;}
  finally {if(browser) await browser.close();fixture.kill('SIGTERM');}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
