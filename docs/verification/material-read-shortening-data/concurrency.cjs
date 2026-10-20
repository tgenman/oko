/* global window, document, MutationObserver */
const {test}=require('node:test');
const {productionReader}=require(process.cwd()+'/tests/browser/http-reader.ts');
const {execFile}=require('node:child_process');
const {promisify}=require('node:util');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
const execute=promisify(execFile);
for(const tabs of [1,5,20])test(`concurrent reading ${tabs} tabs`,{timeout:180000},async t=>{
 const r=await productionReader(t,{collection:false,processingDelay:1,prepare:async data=>{
  await execute(process.cwd()+'/.venv/bin/python',['-m','tests.support.performance_seed',data,'--per-feed','26','--active'],{cwd:process.cwd()});
 }});
 const pages=[r.page];
 for(let i=1;i<tabs;i++)pages.push(await r.context.newPage());
 let checks=0;
 await r.context.addInitScript(()=>{
  window.samples=[];window.clickStart=0;window.expected='';
  document.addEventListener('click',event=>{const row=event.target.closest?.('[data-card]');if(row){window.expected=row.dataset.card;window.clickStart=performance.now();}},true);
  new MutationObserver(()=>{const m=document.querySelector('#material'),text=document.querySelector('#text')?.textContent||'';if(window.expected&&m&&!m.hidden&&text.startsWith('Publication '+window.expected+'\n')){window.samples.push(performance.now()-window.clickStart);window.expected='';}}).observe(document,{childList:true,subtree:true,characterData:true,attributes:true});
 });
 for(const page of pages)page.on('response',r=>{if(r.url().endsWith('/api/v3/reading/check'))checks++;});
 await Promise.all(pages.map(async page=>{await page.goto(r.origin+'/#token='+r.token);await page.waitForFunction(()=>document.querySelectorAll('#cards [data-card]').length===104);}));
 const before=await r.api('status');
 await Promise.all(pages.map(async page=>{
  const ids=await page.locator('#cards [data-card]').evaluateAll(nodes=>nodes.slice(0,20).map(n=>n.dataset.card));
  for(const [i,id] of ids.entries()){
   await page.locator(`[data-card="${id}"]`).click();
   await page.waitForFunction(n=>window.samples.length===n,i+1,{timeout:15000});
  }
 }));
 const values=(await Promise.all(pages.map(p=>p.evaluate(()=>window.samples)))).flat().sort((a,b)=>a-b);
 const after=await r.api('status');
 assert.equal(values.length,tabs*20);
 const result={tabs,materials:104,samples:values,checks,activeBefore:before.work.some(w=>w.phase==='processing'),activeAfter:after.work.some(w=>w.phase==='processing'),n:values.length,p50:values[Math.floor(values.length*.5)],p95:values[Math.ceil(values.length*.95)-1],p99:values[Math.ceil(values.length*.99)-1],max:values.at(-1)};
 assert.ok(result.activeBefore&&result.activeAfter,'Synthetic processing must remain active');
 await fs.writeFile(process.env.OKO_CONCURRENCY_OUTPUT+`/tabs-${tabs}.json`,JSON.stringify(result,null,2));
 console.log('CONCURRENCY',JSON.stringify({...result,samples:undefined}));
});
