import {test} from 'node:test';
import assert from 'node:assert/strict';
import {productionReader} from './http-reader';

test('source commands do not require browser registration',{timeout:20000},async t=>{
 const r=await productionReader(t);let leases=0;
 r.page.on('request',request=>{if(request.url().includes('/client-sessions'))leases++;});
 assert.equal((await r.api('status')).work_allowed,true);
 await r.api('v3/telegram/connect','POST',{source_locator:'@postunavailable',folder:'News'});
 await r.openReader();await r.page.close();
 assert.equal((await r.api('status')).runtime_state,'running');assert.equal(leases,0);
});
