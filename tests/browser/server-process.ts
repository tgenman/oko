interface HostReply {id: number; origin: string; error?: string;}
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {createInterface} from 'node:readline';
import {after} from 'node:test';
import * as path from 'node:path';

let host: ReturnType<typeof createHost> | undefined;
function serverProcess() { return host ??= createHost(); }
function createHost() {
  const root=path.resolve(__dirname,'../..'), venv=path.join(root,'.venv/bin/python');
  const child=spawn(process.env.OKO_PYTHON || (existsSync(venv)?venv:'python3'),
    ['-m','tests.support.browser_host'], {cwd:root,stdio:['pipe','pipe','pipe']});
  const pending=new Map<number, {resolve:(value: HostReply)=>void;reject:(error: unknown)=>void}>();
  let sequence=0, stderr='', failure: unknown;
  function fail(error: unknown) {
    failure ??= error;
    for (const request of pending.values()) request.reject(failure);
    pending.clear();
  }
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-16000);});
  const exit=new Promise<void>(resolve=>{
    child.once('error',error=>{fail(error);resolve();});
    child.once('exit',(code,signal)=>{fail(new Error(`HTTP fixture host exited (${code ?? signal}): ${stderr}`));resolve();});
  });
  child.stdin.on('error',fail);
  const lines=createInterface({input:child.stdout});
  lines.on('line',line=>{
    try {
      const result=JSON.parse(line), request=pending.get(result.id);
      if (!request) return;
      pending.delete(result.id);
      if (result.error) request.reject(new Error(result.error)); else request.resolve(result);
    } catch(error) {fail(error);child.kill('SIGTERM');}
  });
  return {
    async request(op: string,values: Record<string, string | number>={}) {
      if (failure) throw failure;
      const id=++sequence;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        return await new Promise<HostReply>((resolve,reject)=>{
          pending.set(id,{resolve,reject});
          timer=setTimeout(()=>{fail(new Error(`HTTP fixture ${op} timeout`));child.kill('SIGTERM');},15000);
          child.stdin.write(JSON.stringify({id,op,...values})+'\n');
        });
      } finally {clearTimeout(timer);pending.delete(id);}
    },
    async close() {
      child.stdin.end();
      const timer=setTimeout(()=>child.kill('SIGKILL'),4000);
      try {await exit;if(child.exitCode!==0) throw failure;} finally {clearTimeout(timer);lines.close();}
    },
  };
}
export const start = (data: string,token: string) => serverProcess().request('start',{data,token});
export const stop = (scenario: number) => serverProcess().request('stop',{scenario});
after(async()=>{if(host) await host.close();});
