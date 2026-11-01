import {test} from 'node:test';
import assert from 'node:assert/strict';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader.ts';
import {artifactPath} from './artifacts.ts';

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
  assert.deepEqual(result[2]!.methods,['GET']);assert.match(result[2]!.error,/Unsupported Oko API version/);
});
