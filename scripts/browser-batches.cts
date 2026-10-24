// Register test files in bounded workers without owning a browser or Runtime.
import {mkdtemp,writeFile,rm,stat} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

export async function batchTests(args:string[], timings:Record<string,number>={}) {
  let directory:string|undefined;
  try {

      const files=args.filter(value=>value.endsWith('.ts')&&!value.startsWith('-')&&path.basename(value).startsWith('test_'));
      const concurrency=args.find(value=>value.startsWith('--test-concurrency='));
      const workers=Number(concurrency?.split('=')[1]??1);
      if(args.includes('--test')&&files.length>workers&&workers>0) {
        // Register several files per worker. Imports/browser connection/host are
        // reused; each scenario still owns its context, storage and Runtime.
        directory=await mkdtemp(path.join(os.tmpdir(),'oko-browser-batches-'));
        const groups=Array.from({length:workers},()=>({files:[] as string[],size:0}));
        const weighted=await Promise.all(files.map(async file=>({file,size:(await stat(file)).size})));
        const measured=weighted.map(value=>({value,seconds:timings[value.file]??timings[path.relative(process.cwd(),value.file).split(path.sep).join('/')]}))
          .filter((entry):entry is {value:{file:string;size:number};seconds:number}=>typeof entry.seconds==='number'&&Number.isFinite(entry.seconds)&&entry.seconds>0&&entry.seconds<=86400);
        const rates=measured.map(entry=>entry.seconds/Math.max(1,entry.value.size)).sort((a,b)=>a-b);
        const middle=Math.floor(rates.length/2);
        const scale=rates.length?(rates.length%2?rates[middle]!:((rates[middle-1]!+rates[middle]!)/2)):1;
        const hints=new Map(measured.map(entry=>[entry.value.file,entry.seconds]));
        const cost=(value:{file:string;size:number})=>Math.max(.001,hints.get(value.file)??Math.max(1,value.size)*scale);
        weighted.sort((a,b)=>cost(b)-cost(a)||a.file.localeCompare(b.file));
        for(const value of weighted) {
          const group=groups.reduce((a,b)=>a.size<=b.size?a:b);
          group.files.push(value.file);group.size+=cost(value);
        }
        const sizes=new Map(weighted.map(value=>[value.file,value.size]));
        for(const group of groups)group.files.sort((a,b)=>(sizes.get(b)??0)-(sizes.get(a)??0)||a.localeCompare(b));
        const batches=await Promise.all(groups.map(async(group,index)=>{
          const filename=path.join(directory!,`batch-${index}.cjs`);
          await writeFile(filename,group.files.map(file=>`try {require(${JSON.stringify(path.resolve(file))});} catch(error) {process.exitCode=1;require('node:test').test(${JSON.stringify('load '+file)},()=>{throw error;});}`).join('\n'));
          return filename;
        }));
        args=[...args.filter(value=>!files.includes(value)),...batches];
      }
    return {args,close:async()=>{if(directory)await rm(directory,{recursive:true,force:true});}};
  } catch(error) {
    if(directory)await rm(directory,{recursive:true,force:true});
    throw error;
  }
}
