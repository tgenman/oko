import {build} from 'vite';
import {createHash} from 'node:crypto';
import {promises as fs} from 'node:fs';
import {resolve, relative, join} from 'node:path';
import {execFileSync} from 'node:child_process';
import ts from 'typescript';

const root = resolve(import.meta.dirname, '..');
const output = join(root, 'oko/frontend_build');
const inputs = ['frontend', 'api/openapi.yaml', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.mjs', 'scripts/build-frontend.mjs'];
async function files(directory) {
  const found = [];
  for (const entry of (await fs.readdir(directory, {withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) found.push(...await files(path));
    else if (entry.isFile()) found.push(path);
  }
  return found.sort();
}
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
async function sourceDigest() {
  const hash = createHash('sha256');
  for (const input of inputs) {
    const path = join(root, input), stat = await fs.stat(path);
    for (const file of stat.isDirectory() ? await files(path) : [path]) {
      hash.update(relative(root,file).split('\\').join('/')); hash.update('\0');
      hash.update(await fs.readFile(file)); hash.update('\0');
    }
  }
  return hash.digest('hex');
}
async function compile() {
  execFileSync(process.execPath,[join(root,'scripts/generate-api.mjs'),'check'],{stdio:'inherit'});
  execFileSync(process.execPath,[join(root,'node_modules/typescript/bin/tsc'),'--noEmit'],{cwd:root,stdio:'inherit'});
  const before = await sourceDigest();
  const stage = join(output, '.stage-' + process.pid);
  await fs.mkdir(output,{recursive:true});
  await fs.rm(stage,{recursive:true,force:true});
  try {
    const result = await build({configFile:join(root,'vite.config.mjs'),build:{outDir:stage,emptyOutDir:true}});
    await fs.mkdir(join(stage,'docs'),{recursive:true});
    await fs.copyFile(join(root,'frontend/docs/index.html'),join(stage,'docs/index.html'));
    const initializer=ts.transpileModule(await fs.readFile(join(root,'frontend/docs/initializer.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}});
    await fs.writeFile(join(stage,'docs/initializer.js'),initializer.outputText);
    for (const name of ['swagger-ui.css','swagger-ui-bundle.js']) {
      await fs.copyFile(join(root,'node_modules/swagger-ui-dist',name),join(stage,'docs',name));
    }
    // These resources use runtime URLs in the unchanged reader.
    for (const name of ['icons','licenses']) await fs.cp(join(root,'frontend',name),join(stage,name),{recursive:true});
    // The paint-only layer remains independently toggleable in browser checks,
    // while its URL belongs to the same immutable generation as the reader.
    const surface = await fs.readFile(join(stage,'reader-surfaces.css'));
    const surfaceName = 'assets/reader-surfaces-' + digest(surface).slice(0,16) + '.css';
    await fs.rename(join(stage,'reader-surfaces.css'),join(stage,surfaceName));
    const index = join(stage,'index.html');
    await fs.writeFile(index,(await fs.readFile(index,'utf8')).replace('/reader-surfaces.css','/'+surfaceName));
    // A stable transport entry is retained for its public browser boundary;
    // production reader imports always refer to hashed assets.
    const bundles = Array.isArray(result) ? result : [result];
    const transport = bundles.flatMap(bundle=>bundle.output || []).find(chunk=>chunk.type==='chunk' && chunk.isEntry && chunk.name==='transport')?.fileName;
    if (!transport) throw new Error('Transport entry missing from the frontend bundle');
    await fs.writeFile(join(stage,'transport.js'),'export * from "/'+transport+'";\n');
    if (before !== await sourceDigest()) throw new Error('Sources changed during build; retry');
    const assets = {};
    for (const file of await files(stage)) assets[relative(stage,file).split('\\').join('/')] = digest(await fs.readFile(file));
    const generation = digest(JSON.stringify(assets));
    const target = join(output,'generations',generation);
    await fs.mkdir(join(output,'generations'),{recursive:true});
    try {await fs.rename(stage,target);} catch(error) {
      if (!['EEXIST','ENOTEMPTY'].includes(error.code)) throw error;
      await fs.rm(stage,{recursive:true,force:true});
    }
    const temporary = join(output,'.current-' + process.pid);
    const contract = digest(await fs.readFile(join(root,'oko/reader/generated/openapi.json')));
    await fs.writeFile(temporary,JSON.stringify({version:1,source:before,generation,contract,assets},null,2)+'\n');
    await fs.rename(temporary,join(output,'current.json'));
  } finally {await fs.rm(stage,{recursive:true,force:true});}
}
if (process.argv.includes('--check')) {
  const manifest = JSON.parse(await fs.readFile(join(output,'current.json'),'utf8'));
  if (manifest.source !== await sourceDigest()) throw new Error('Frontend build is stale. Run npm run build');
  for (const [name,hash] of Object.entries(manifest.assets)) if (digest(await fs.readFile(join(output,'generations',manifest.generation,name))) !== hash) throw new Error('Frontend asset corrupt: '+name);
} else {
  await compile();
  if (process.argv.includes('--watch')) {
    let active = false, pending = false;
    const rebuild = async () => {
      pending = true; if(active) return; active=true;
      while(pending) {pending=false; try {await compile();} catch(error) {console.error(error.message);}}
      active=false;
    };
    let seen = await sourceDigest();
    // Poll content rather than relying on platform-specific recursive watchers.
    // This also observes atomic file replacements used by editors.
    setInterval(async () => {
      try {
        const next = await sourceDigest();
        if(next !== seen) {seen=next; await rebuild();}
      } catch(error) {console.error(error.message);}
    },250);
    console.log('Watching frontend; Python serves the latest complete generation.');
  }
}
