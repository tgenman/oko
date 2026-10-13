const {spawn} = require('node:child_process');
const {existsSync} = require('node:fs');
const path = require('node:path');
const {createInterface} = require('node:readline');
const {after} = require('node:test');

let host;
function serverProcess() {
  if (host) return host;
  const root=path.resolve(__dirname,'../..'), venv=path.join(root,'.venv/bin/python');
  const child=spawn(process.env.OKO_PYTHON || (existsSync(venv)?venv:'python3'),
    ['-m','tests.support.browser_host'], {cwd:root,stdio:['pipe','pipe','pipe']});
  const pending=new Map();
  let sequence=0, stderr='', failure;
  function fail(error) {
    failure ??= error;
    for (const request of pending.values()) request.reject(failure);
    pending.clear();
  }
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-16000);});
  const exit=new Promise(resolve=>{
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
  host={
    async request(op,values={}) {
      if (failure) throw failure;
      const id=++sequence;
      let timer;
      try {
        return await new Promise((resolve,reject)=>{
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
  return host;
}
exports.start = (data,token) => serverProcess().request('start',{data,token});
exports.stop = scenario => serverProcess().request('stop',{scenario});
after(async()=>{if(host) await host.close();});
