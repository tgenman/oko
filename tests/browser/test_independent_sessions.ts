import {test} from 'node:test';
import assert from 'node:assert/strict';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader.ts';
import {artifactPath} from './artifacts.ts';

// The web session and a separate non-browser client share the real HTTP host.
test('neutral web and independent HTTP sessions coexist and close independently', {timeout:45000, skip:'Временно отключён по просьбе пользователя 2026-10-15; повторная проверка coexistence/BFCache отложена.'}, async t => {
  const r=await productionReader(t);
  const headers={Authorization:'Bearer '+r.token};
  const getStatus=async()=>{
    const reply=await fetch(r.origin+'/api/v3/status',{headers});
    assert.equal(reply.status,200,await reply.clone().text());
    return reply.json();
  };
  const errors:string[]=[];r.page.on('pageerror',error=>errors.push(error.message));
  await r.page.goto(r.origin+'/#token='+r.token);
  await expect.poll(async()=>(await getStatus()).active_client_sessions).toBe(1);
  const first=await getStatus();
  assert.deepEqual(first.lifetime,{kind:'client_presence',lease_seconds:60,grace_seconds:60,renew_after_seconds:2});
  const created=await fetch(r.origin+'/api/v3/client-sessions',{method:'POST',headers});
  assert.equal(created.status,201);
  const independent=await created.json();
  await expect.poll(async()=>(await getStatus()).active_client_sessions).toBe(2);
  assert.equal((await fetch(r.origin+'/api/v3/client-sessions/'+independent.id,{method:'DELETE',headers})).status,200);
  await expect.poll(async()=>(await getStatus()).active_client_sessions).toBe(1);
  const stale=await fetch(r.origin+'/api/v3/client-sessions/'+independent.id,{method:'PUT',headers});
  assert.equal(stale.status,410);assert.equal((await stale.json()).code,'client_session_expired');
  await r.page.screenshot({path:await artifactPath('independent-sessions-desktop.png'),fullPage:true});
  await r.page.setViewportSize({width:390,height:844});
  await r.page.screenshot({path:await artifactPath('independent-sessions-mobile.png'),fullPage:true});
  const reload=r.page.waitForEvent('framenavigated');
  await r.page.evaluate(()=>{
    window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));
    window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
  });
  await reload;
  await expect.poll(async()=>(await getStatus()).active_client_sessions).toBe(1);
  assert.deepEqual(errors,[]);
});

test('independent SDK accepts compatible versions and rejects another major before mutation', {timeout:45000}, async t=>{
  const r=await productionReader(t);
  await r.page.goto(r.origin+'/#token='+r.token);
  const result=await r.page.evaluate(async()=>{
    const {createReaderApi}=await import('/transport.js');
    const results=[];
    for(const version of ['3.1.0','3.0.1','4.0.0']) {
      const methods:string[]=[];
      const api=createReaderApi({token:'synthetic',origin:location.origin,fetch:async(path,options)=>{
        methods.push(options?.method || 'GET');
        return Response.json(String(path).endsWith('/status')?{api_version:version}:{ok:true,command_id:'compatible'});
      }});
      let error='';try{await api.stop();}catch(caught){error=String(caught);}
      results.push({version,methods,error});
    }
    return results;
  });
  for(const entry of result.slice(0,2)) {assert.equal(entry.error,'');assert.deepEqual(entry.methods,['GET','POST']);}
  assert.deepEqual(result[2]!.methods,['GET']);assert.match(result[2]!.error,/Версия API/);
});
