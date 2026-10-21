import {test} from 'node:test';
import {expect} from 'playwright/test';
import assert from 'node:assert/strict';
import {promisify} from 'node:util';
import {execFile} from 'node:child_process';
import {existsSync} from 'node:fs';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

const execute=promisify(execFile),root=path.resolve(__dirname,'../..');
const python=process.env.OKO_PYTHON||(existsSync(path.join(root,'.venv/bin/python'))?path.join(root,'.venv/bin/python'):'python3');
type Sample={id:string;phase:'first'|'repeat';ms:number};
declare global {interface Window {readingBenchmark:{expected:string;started:number;phase:Sample['phase'];samples:Sample[]};}}

for(const perFeed of [26,100])for(const active of [false,true]){
 test(`saved reading latency: ${perFeed*4} materials, ${active?'active processing':'idle'}`,{timeout:90000},async t=>{
  const r=await productionReader(t,{collection:false,processingDelay:active?.15:0,prepare:async directory=>{
    await execute(python,['-m','tests.support.performance_seed',directory,'--per-feed',String(perFeed),...(active?['--active']:[])],{cwd:root,signal:t.signal});
  }});
  await r.page.addInitScript(()=>{
    const state=window.readingBenchmark={expected:'',started:0,phase:'first' as const,samples:[] as Sample[]};
    document.addEventListener('click',event=>{
      const row=event.target instanceof Element?event.target.closest<HTMLElement>('[data-card]'):null;
      if(row?.dataset.card){state.expected=row.dataset.card;state.started=performance.now();}
    },true);
    new MutationObserver(()=>{
      if(!state.expected)return;
      const material=document.getElementById('material'),text=document.getElementById('text')?.textContent||'';
      if(material&&!material.hidden&&text.startsWith('Publication '+state.expected+'\n')){
        state.samples.push({id:state.expected,phase:state.phase,ms:performance.now()-state.started});state.expected='';
      }
    }).observe(document,{childList:true,subtree:true,characterData:true,attributes:true});
  });
  await r.page.goto(r.origin+'/#token='+r.token);
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(perFeed*4);
  if(active)await expect.poll(async()=>(await r.api('status')).work.some(w=>w.phase==='processing')).toBe(true);
  const ids=await r.page.locator('#cards [data-card]').evaluateAll(nodes=>nodes.slice(0,40).map(n=>(n as HTMLElement).dataset.card!));
  let expected=0;
  const work=[];
  for(const phase of ['first','repeat'] as const){
    await r.page.evaluate(phase=>{window.readingBenchmark.phase=phase;},phase);
    for(const id of ids){
      await r.page.locator(`[data-card="${id}"]`).click();
      await r.page.waitForFunction(expected=>window.readingBenchmark.samples.length===expected,++expected,{polling:'raf'});
    }
    work.push((await r.api('status')).work);
  }
  const samples=await r.page.evaluate(()=>window.readingBenchmark.samples);
  const result={materials:perFeed*4,active,work,samples,series:(['first','repeat'] as const).map(phase=>{
    const values=samples.filter(s=>s.phase===phase).map(s=>s.ms).sort((a,b)=>a-b);
    const p95=values[Math.ceil(values.length*.95)-1];
    return {phase,n:values.length,p50:values[Math.floor(values.length*.5)],p95,max:values.at(-1)};
  })};
  console.log('READING_LATENCY',JSON.stringify({...result,samples:undefined}));
  await writeFile(artifactPath(`latency-${perFeed*4}-${active?'active':'idle'}.json`),JSON.stringify(result,null,2));
  await r.page.screenshot({path:artifactPath(`latency-${perFeed*4}-${active?'active':'idle'}.png`)});
  for(const series of result.series){assert.equal(series.n,40);assert.ok(series.p95<200,JSON.stringify(series));}
  if(active)assert.ok(work.every(w=>w.some(p=>p.phase==='processing')),'Background work must remain active during both series');
 });
}
