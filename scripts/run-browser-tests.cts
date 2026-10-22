// Own Chromium for one node --test command. Scenarios still own fresh contexts.
import {chromium} from 'playwright';
import {browserLaunchOptions} from './browser-launch.mts';
import {spawn,execFileSync,type ChildProcess} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import {batchTests} from './browser-batches.cts';

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
  let batch:Awaited<ReturnType<typeof batchTests>>|undefined;
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
      batch=await batchTests(process.argv.slice(2));
      const args=batch.args;
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
    await batch?.close();
  }
}
void main().catch(error=>{console.error(error);process.exitCode=1;});
