// Explicit generation writes derived files; check generates into temp only.
import {createClient} from '@hey-api/openapi-ts';
import {mkdtemp, readFile, writeFile, readdir, rm, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = process.env.OKO_PYTHON || path.join(root, '.venv/bin/python');
const command = process.argv[2] || 'check';
// Validate the inherited context before validation, generation or nested builds.
if (process.env.OKO_CANDIDATE_CONTEXT) {
  execFileSync(python,[path.join(root,'scripts/candidate.py')],{stdio:'pipe'});
}
if (['compat','baseline'].includes(command)) {
  execFileSync(python,[path.join(root,'scripts/api-compat.py'),command==='compat'?'check':'baseline',...process.argv.slice(3)],{stdio:'inherit'});
  process.exit(0);
}
if (['validate','docs'].includes(command)) {
  execFileSync(python,[path.join(root,'scripts/api.py'),command,...process.argv.slice(3)],{stdio:'inherit'});
  process.exit(0);
}
if (!['generate','check'].includes(command)) throw new Error('Expected generate or check');
execFileSync(python, [path.join(root,'scripts/api.py'),'validate'], {stdio:'inherit'});
execFileSync(python, [path.join(root,'scripts/api-compat.py'),'check'], {stdio:'inherit'});
execFileSync(python, [path.join(root,'scripts/generate_api.py'),command], {stdio:'inherit'});
// Expand–contract only: regenerate remaining v2 consumers from the unchanged
// reviewed baseline. Remove this bridge after the final v3 consumer migrates.
const canonical = JSON.parse(execFileSync(python,[path.join(root,'scripts/api.py'),'bundle']).toString());
const transitional = canonical.info.version.startsWith('3.');
if (transitional) execFileSync(python,[path.join(root,'scripts/generate_api.py'),command,'--schema',path.join(root,'api/baseline.json'),'--output',path.join(root,'oko/reader/legacy_generated')],{stdio:'inherit'});
for (const legacy of (transitional ? [false,true] : [false])) {
const temporary = await mkdtemp(path.join(tmpdir(),'oko-sdk-'));
const saved = path.join(root,legacy ? 'frontend/src/api/legacy-generated' : 'frontend/src/api/generated');
async function files(directory: string, prefix=''): Promise<Map<string, Buffer>> {
  const result = new Map<string, Buffer>();
  for (const entry of await readdir(directory,{withFileTypes:true})) {
    const name = path.join(prefix,entry.name);
    if (entry.isDirectory()) for (const [key,bytes] of await files(path.join(directory,entry.name),name)) result.set(key,bytes);
    else result.set(name,await readFile(path.join(directory,entry.name)));
  }
  return result;
}
try {
  const bundle = legacy ? await readFile(path.join(root,'api/baseline.json')) : execFileSync(python,[path.join(root,'scripts/api.py'),'bundle']);
  const digest = createHash('sha256').update(bundle).digest('hex');
  const source = path.join(temporary,'openapi.json');
  await writeFile(source,bundle);
  const output = path.join(temporary,'library');
  await createClient({input:source,output,plugins:[
    '@hey-api/typescript',
    {name:'@hey-api/client-fetch',throwOnError:true},
    {name:'@hey-api/sdk',auth:false},
  ]});
  type Operation = {responses?:Record<string,{content?:Record<string,{schema?:{format?:string}}>}>};
  const document: {paths: Record<string,Record<string,Operation>>}=JSON.parse(bundle.toString('utf8'));
  const binary=[];
  for(const [route,methods] of Object.entries(document.paths))for(const [method,operation] of Object.entries(methods)) {
    const media=operation.responses?.['200']?.content || {};
    if(Object.values(media).some(reply=>reply.schema?.format==='binary')) {
      const pattern='^'+route.split(/(\{[^}]+\})/).map(part=>part.startsWith('{')?'[^/]+':part.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('')+'$';
      binary.push(`{method:${JSON.stringify(method.toUpperCase())},path:new RegExp(${JSON.stringify(pattern)})}`);
    }
  }
  await writeFile(path.join(output,'media.gen.ts'),`// Generated from canonical responses; do not edit.\nconst routes=[${binary.join(',')}];\nexport const isBinaryOperation=(path:string,method:string)=>routes.some(route=>route.method===method&&route.path.test(path));\n`);
  await writeFile(path.join(output,'revision.ts'),`// Generated; do not edit.\nexport const CONTRACT_DIGEST = '${digest}';\n`);
  const expected = await files(output);
  if (command==='generate') {
    await rm(saved,{recursive:true,force:true});
    for (const [name,bytes] of expected) {
      await mkdir(path.dirname(path.join(saved,name)),{recursive:true});
      await writeFile(path.join(saved,name),bytes);
    }
  } else {
    let actual;
    try {actual=await files(saved);} catch {actual=new Map();}
    const changed = [...new Set([...expected.keys(),...actual.keys()])].filter(name=>!expected.get(name)?.equals(actual.get(name) || Buffer.alloc(0)));
    if (changed.length) throw new Error('Stale TypeScript API library: '+changed.join(', ')+'. Run npm run api:generate.');
    process.stdout.write('TypeScript API library is current\n');
  }
} finally {await rm(temporary,{recursive:true,force:true});}

}
