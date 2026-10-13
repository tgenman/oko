const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {randomBytes} = require('node:crypto');
const servers = require('./server-process.cjs');
const {newContext} = require('./browser-context.cjs');
const assert = require('node:assert/strict');

exports.productionReader = async function(t) {
  const data=await fs.mkdtemp(path.join(os.tmpdir(),'oko-history-http-'));
  const token=randomBytes(24).toString('base64url');
  let context, scenario, cleanup;
  // Startup is awaited before cleanup, even when abort arrives during startup.
  const started=servers.start(data,token).then(value=>{scenario=value;return value;});
  const close=()=>cleanup ??= (async()=>{
    t?.signal.removeEventListener('abort',abort);
    try {await started;} catch { /* Startup failure is reported to the caller. */ }
    try {await context?.close();} finally {
      try {if(scenario) await servers.stop(scenario.id);}
      finally {await fs.rm(data,{recursive:true,force:true});}
    }
  })();
  const abort=()=>{void close().catch(()=>{});};
  t?.signal.addEventListener('abort',abort,{once:true});
  t?.after(close);
  try {
    const {origin}=await started;
    t?.signal.throwIfAborted();
    const headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'};
    async function api(url,method='GET',body) {
      const response=await fetch(origin+'/api/'+url,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
      assert.equal(response.status,200,await response.clone().text()); return response.json();
    }
    context=await newContext();
    if(t?.signal.aborted) {await context.close();t.signal.throwIfAborted();}
    const page=await context.newPage();
    t?.signal.throwIfAborted();
    return {page,context,api,origin,token,close};
  } catch(error) {await close();throw error;}
};
