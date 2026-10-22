import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {expect} from 'playwright/test';
import path from 'node:path';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import os from 'node:os';

function descendants(root:number) {
  const rows=execFileSync('ps',['-axo','pid=,ppid='],{encoding:'utf8'}).trim().split('\n').map(line=>line.trim().split(/\s+/).map(Number));
  const pids=new Set([root]);
  for(let changed=true;changed;){changed=false;for(const [pid,parent] of rows)if(pids.has(parent)&&!pids.has(pid)){pids.add(pid);changed=true;}}
  return [...pids];
}
function alive(pid:number){try{process.kill(pid,0);return true;}catch{return false;}}

for(const outcome of ['success','failure','interrupt'] as const)test('suite browser releases every owned process after '+outcome,{timeout:20000},async t=>{
  const script=path.resolve(__dirname,'../../scripts/run-browser-tests.cts');
  let fixture=`
    const {chromium}=require('playwright');
    const assert=require('node:assert/strict');
    (async()=>{
      const a=await chromium.connect(process.env.OKO_TEST_BROWSER_WS);
      const b=await chromium.connect(process.env.OKO_TEST_BROWSER_WS);
      const x=await a.newContext(),y=await b.newContext();
      await x.addCookies([{name:'secret',value:'first',url:'http://fixture.test'}]);
      assert.deepEqual(await y.cookies(),[]);
      const page=await y.newPage();await page.setContent('<title>Independent</title>');
      await a.close();assert.equal(await page.title(),'Independent');
      console.log('READY');
      process.stdin.once('data',async()=>{await b.close();process.exitCode=${outcome==='failure'?7:0};});
    })().catch(error=>{console.error(error);process.exitCode=1;});
  `;
  const workerCode=(outcome==='failure'?"process.on('SIGTERM',()=>{});":"")+"setInterval(()=>{},1000);process.send('ready');";
  fixture=fixture.replace("console.log('READY');",`const worker=require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(workerCode)}],{stdio:['ignore','ignore','ignore','ipc']});worker.once('message',()=>{worker.disconnect();console.log('READY');});worker.unref();`);
  let args=['-e',fixture];
  if(outcome==='interrupt') {
    const directory=await mkdtemp(path.join(os.tmpdir(),'oko-suite-owner-'));
    t.after(()=>rm(directory,{recursive:true,force:true}));
    const file=path.join(directory,'fixture.cjs');
    fixture=fixture.replace("require('playwright')",'require('+JSON.stringify(require.resolve('playwright'))+')');
    await writeFile(file,"require('node:test').test('live descendant',async()=>{\n"+fixture+"\nawait new Promise(()=>{});});");
    args=['--test',file];
  }
  const env={...process.env};delete env.NODE_TEST_CONTEXT;
  const child=spawn(process.execPath,['--require','tsx/cjs',script,...args],{stdio:['pipe','pipe','pipe'],env});
  let stderr='';child.stderr.on('data',chunk=>{stderr+=chunk;});
  const exited=once(child,'exit');
  t.after(async()=>{if(child.exitCode===null&&child.signalCode===null)child.kill('SIGTERM');await exited;});
  await new Promise<void>((resolve,reject)=>{
    let output='';child.stdout.on('data',chunk=>{output+=chunk;if(output.includes('READY'))resolve();});
    child.once('error',reject);child.once('exit',()=>reject(new Error('Fixture exited before readiness: '+stderr+'\n'+output)));
  });
  const owned=descendants(child.pid!);
  t.after(()=>{for(const pid of owned)if(alive(pid))try{process.kill(pid,'SIGKILL');}catch{}});
  assert.ok(owned.length>2,'Owner must have launched a browser and a test child');
  if(outcome==='interrupt')child.kill('SIGINT');else child.stdin.end('finish');
  const [code]=await exited;
  await expect.poll(()=>owned.filter(alive),{timeout:5000}).toEqual([]);
  assert.equal(code,outcome==='interrupt'?130:outcome==='failure'?7:0,stderr);
});
