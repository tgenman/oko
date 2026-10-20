import {test} from 'node:test';
import {artifactPath} from './artifacts.ts';
import {reader, material, expect} from './support.ts';

test('feed selection finishes while an older refresh is still in flight', async () => {
 const r=await reader(); try {
  await r.page.addInitScript(()=>{
   const original=window.fetch;
   window.refreshProbe={enabled:false,oldStarted:false,feedRequests:0};
   window.fetch=async(url,options)=>{
    const key=String(url),probe=window.refreshProbe;
    if(probe.enabled&&key==='/api/v3/reading/check'&&!JSON.parse(String(options?.body)).resources.some((r:{kind:string;options?:{target:{scope:string}}})=>r.kind==='list'&&r.options?.target.scope==='feed')&&!probe.oldStarted){
     probe.oldStarted=true;
     await new Promise(resolve=>setTimeout(resolve,200));
    }else if(probe.enabled&&key==='/api/v3/reading/check'&&JSON.parse(String(options?.body)).resources.some((r:{kind:string;options?:{target:{scope:string}}})=>r.kind==='list'&&r.options?.target.scope==='feed')){
     probe.feedRequests++;
     await new Promise(resolve=>setTimeout(resolve,900));
    }
    return original(url,options);
   };
  });
  await r.start([material('one')]);
  await r.page.evaluate(()=>{
   const t=readerTransport;
   t.replies.set('/api/v3/cards?sort=newest&feed=feed',{body:t.replies.get('/api/v3/cards')!.body});
   window.refreshProbe.enabled=true;
  });
  await r.page.waitForFunction(()=>window.refreshProbe.oldStarted);
  await r.page.locator('[data-feed="feed"]').click();
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(1,{timeout:2000});
  expect(await r.page.evaluate(()=>window.refreshProbe.feedRequests)).toBe(1);
  await r.page.screenshot({path:artifactPath('feed-refresh.png')});
 } finally {await r.close();}
});
