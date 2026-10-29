import {test} from 'node:test';
import assert from 'node:assert/strict';
import {productionReader} from './http-reader';

test('opening reader waits for actual browser registration before source commands',{timeout:20000},async t=>{
 const r=await productionReader(t);
 let release=()=>{},seen=()=>{},opened=false;
 const gate=new Promise<void>(resolve=>release=resolve),requested=new Promise<void>(resolve=>seen=resolve);
 await r.page.route('**/api/v3/client-sessions',async route=>{seen();await gate;await route.continue();});
 const opening=r.openReader().then(()=>{opened=true;});
 try{
  await requested;await r.page.waitForLoadState('load');
  assert.equal((await r.api('v3/status/details')).active_client_sessions,0);
  assert.equal(opened,false,'openReader must keep waiting while registration is held');
  release();await opening;
  await r.api('v3/telegram/connect','POST',{source_locator:'@postunavailable',folder:'News'});
 }finally{release();await opening;}
});
