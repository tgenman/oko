const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const {existsSync} = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {once} = require('node:events');
const {createInterface} = require('node:readline');
const {chromium, expect} = require('playwright/test');
const assert = require('node:assert/strict');

exports.productionReader = async function(t) {
  const root=path.resolve(__dirname,'../..'), data=await fs.mkdtemp(path.join(os.tmpdir(),'oko-history-http-'));
  const venv=path.join(root,'.venv/bin/python'), python=process.env.OKO_PYTHON || (existsSync(venv)?venv:'python3');
  const child=spawn(python,['-m','tests.support.browser_server',data],{cwd:root,stdio:['ignore','pipe','pipe']});
  let browser, stderr=''; child.stderr.on('data',chunk=>{stderr+=chunk;});
  const exit=once(child,'exit'); exit.catch(()=>{});
  const abort=()=>{child.kill('SIGTERM'); void browser?.close().catch(()=>{});};
  t.signal.addEventListener('abort',abort,{once:true});
  t.after(async()=>{
    t.signal.removeEventListener('abort',abort);
    try {await browser?.close();} finally {
      if(child.exitCode===null&&child.signalCode===null){
        child.kill('SIGTERM'); const timer=setTimeout(()=>child.kill('SIGKILL'),3000);
        try{await exit;}finally{clearTimeout(timer);}
      }
      await fs.rm(data,{recursive:true,force:true});
    }
  });
  const lines=createInterface({input:child.stdout});
  let timer, first;
  try {
    first=await Promise.race([once(lines,'line'),exit.then(()=>{throw new Error(stderr);}),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('HTTP fixture startup timeout')),10000);})]);
  } finally {clearTimeout(timer); lines.close();}
  const {origin,token}=JSON.parse(first[0]);
  const headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'};
  async function api(url,method='GET',body) {
    const response=await fetch(origin+'/api/'+url,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
    assert.equal(response.status,200,await response.clone().text()); return response.json();
  }
  await expect.poll(async()=>{try{return(await fetch(origin)).status;}catch{return 0;}},{timeout:10000}).toBe(200);
  browser=await chromium.launch({headless:true,executablePath:process.env.OKO_BROWSER_EXECUTABLE||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page=await browser.newPage();
  return {page,api,origin,token};
};
