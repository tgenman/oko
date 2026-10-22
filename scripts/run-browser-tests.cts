// Own Chromium for one node --test command. Scenarios still own fresh contexts.
import {chromium} from 'playwright';
import {browserLaunchOptions} from './browser-launch.mts';
import {spawn,execFileSync,type ChildProcess} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import {mkdtemp,writeFile,rm,stat} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

function groupAlive(group:number) {
  return execFileSync('ps',['-axo','pgid=,stat='],{encoding:'utf8'}).trim().split('\n').some(line=>{
    const [id,state]=line.trim().split(/\s+/);return Number(id)===group&&!state?.startsWith('Z');
  });
}
function signalGroup(pid:number,signal:NodeJS.Signals) {
  try {process.kill(-pid,signal);return true;}
  catch(error){
    const code=(error as NodeJS.ErrnoException).code;
    if(code==='ESRCH'||code==='EPERM'&&!groupAlive(pid))return false;
    throw error;
  }
}
async function closeGroup(pid:number) {
  if(!groupAlive(pid)||!signalGroup(pid,'SIGTERM'))return;
  const deadline=performance.now()+1000;
  while(groupAlive(pid)&&performance.now()<deadline)await delay(10);
  if(groupAlive(pid))signalGroup(pid,'SIGKILL');
}

async function main() {
  let child:ChildProcess|undefined, stopped:NodeJS.Signals|undefined;
  let directory:string|undefined;
  const forward=(signal:NodeJS.Signals)=>{
    stopped=signal;
    if(child?.pid)signalGroup(child.pid,signal);
  };
  const interrupt=()=>forward('SIGINT'),terminate=()=>forward('SIGTERM');
  process.on('SIGINT',interrupt);process.on('SIGTERM',terminate);
  try {
    const browser = await chromium.launchServer({...browserLaunchOptions(),host:'127.0.0.1',
      handleSIGINT:false,handleSIGTERM:false,
      args:['--host-resolver-rules=MAP oko 127.0.0.1','--no-proxy-server']});
    try {
      if(stopped){process.exitCode=stopped==='SIGINT'?130:143;return;}
      let args=process.argv.slice(2);
      const files=args.filter(value=>value.endsWith('.ts')&&!value.startsWith('-')&&path.basename(value).startsWith('test_'));
      const concurrency=args.find(value=>value.startsWith('--test-concurrency='));
      const workers=Number(concurrency?.split('=')[1]??1);
      if(args.includes('--test')&&files.length>workers&&workers>0) {
        // Register several files per worker. Imports/browser connection/host are
        // reused; each scenario still owns its context, storage and Runtime.
        directory=await mkdtemp(path.join(os.tmpdir(),'oko-browser-batches-'));
        const groups=Array.from({length:workers},()=>({files:[] as string[],size:0}));
        const weighted=await Promise.all(files.map(async file=>({file,size:(await stat(file)).size})));
        weighted.sort((a,b)=>b.size-a.size||a.file.localeCompare(b.file));
        for(const value of weighted) {
          const group=groups.reduce((a,b)=>a.size<=b.size?a:b);
          group.files.push(value.file);group.size+=value.size;
        }
        const batches=await Promise.all(groups.map(async(group,index)=>{
          const filename=path.join(directory!,`batch-${index}.cjs`);
          await writeFile(filename,group.files.map(file=>`try {require(${JSON.stringify(path.resolve(file))});} catch(error) {process.exitCode=1;require('node:test').test(${JSON.stringify('load '+file)},()=>{throw error;});}`).join('\n'));
          return filename;
        }));
        args=[...args.filter(value=>!files.includes(value)),...batches];
      }
      child = spawn(process.execPath,args,{
        detached:true,stdio:'inherit',env:{...process.env,OKO_TEST_BROWSER_WS:browser.wsEndpoint()},
      });
      const code = await new Promise<number>((resolve,reject)=>{
        child!.once('error',reject);
        child!.once('exit',(code,signal)=>resolve(code ?? (signal==='SIGINT'?130:1)));
      });
      process.exitCode=stopped==='SIGINT'?130:stopped?143:code;
    } finally {
      try {if(child?.pid)await closeGroup(child.pid);}
      finally {await browser.close();}
    }
  } finally {
    process.off('SIGINT',interrupt);process.off('SIGTERM',terminate);
    if(directory)await rm(directory,{recursive:true,force:true});
  }
}
void main().catch(error=>{console.error(error);process.exitCode=1;});
