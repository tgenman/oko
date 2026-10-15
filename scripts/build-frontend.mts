import {checkTypes} from './check-types.mts';
import {validateAssets} from './frontend-assets.mts';
import type {BinaryLike} from 'node:crypto';
import {cached, fingerprint, remember, toolchain} from './api-cache.mts';
import {createHash} from 'node:crypto';
import {promises as fs} from 'node:fs';
import {resolve, relative, join} from 'node:path';
import {execFileSync} from 'node:child_process';


const root = resolve(import.meta.dirname, '..');
const output = join(root, 'oko/frontend_build');
const inputs = ['frontend', 'api/openapi.yaml', 'package.json', 'package-lock.json', 'tsconfig.json', 'tsconfig.tooling.json', 'vite.config.mts', 'scripts/build-frontend.mts'];
async function files(directory: string): Promise<string[]> {
  const found = [];
  for (const entry of (await fs.readdir(directory, {withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) found.push(...await files(path));
    else if (entry.isFile()) found.push(path);
  }
  return found.sort();
}
const digest = (bytes: BinaryLike) => createHash('sha256').update(bytes).digest('hex');
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
async function checkAssets(expected?: string) {
  await validateAssets(output, await sourceDigest(), digest(await fs.readFile(join(root, 'oko/reader/generated/openapi.json'))), expected);
}
async function compile() {
  // Vite defaults this variable during build; normalize it before fingerprinting.
  process.env.NODE_ENV ??= 'production';
  execFileSync(process.execPath,['--import','tsx',join(root,'scripts/generate-api.mts'),'check'],{stdio:'inherit'});
  const key = async () => fingerprint(root, ['frontend', 'api/openapi.yaml', 'oko/reader/generated', 'scripts',
    'tsconfig.json', 'tsconfig.tooling.json', 'vite.config.mts', 'eslint.config.ts'], await toolchain(root));
  const checked = await key();
  if (await cached(root, 'frontend', checked)) {
    try {
      await checkAssets(checked);
      if (checked !== await key()) throw new Error('Build inputs changed during checking');
      console.log('Frontend build cache hit (types and asset integrity verified)');
      return;
    } catch { /* Rebuild missing, stale or damaged output from verified sources. */ }
  }
  await checkTypes(root, 'tsconfig.tooling.json');
  await checkTypes(root, 'tsconfig.json');
  const {build} = await import('vite');
  const {default: ts} = await import('typescript');
  const before = await sourceDigest();
  const stage = join(output, '.stage-' + process.pid);
  await fs.mkdir(output,{recursive:true});
  await fs.rm(stage,{recursive:true,force:true});
  try {
    const result = await build({configFile:join(root,'vite.config.mts'),build:{outDir:stage,emptyOutDir:true}});
    await fs.mkdir(join(stage,'docs'),{recursive:true});
    await fs.copyFile(join(root,'frontend/docs/index.html'),join(stage,'docs/index.html'));
    const initializer=ts.transpileModule(await fs.readFile(join(root,'frontend/docs/initializer.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}});
    await fs.writeFile(join(stage,'docs/initializer.js'),initializer.outputText);
    for (const name of ['swagger-ui.css','swagger-ui-bundle.js']) {
      await fs.copyFile(join(root,'node_modules/swagger-ui-dist',name),join(stage,'docs',name));
    }
    // These resources use runtime URLs in the unchanged reader.
    await fs.cp(join(root,'frontend/icons'),join(stage,'icons'),{recursive:true});
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
    const transport = bundles.flatMap(bundle=>'output' in bundle ? bundle.output : []).find(chunk=>chunk.type==='chunk' && chunk.isEntry && chunk.name==='transport')?.fileName;
    if (!transport) throw new Error('Transport entry missing from the frontend bundle');
    await fs.writeFile(join(stage,'transport.js'),'export * from "/'+transport+'";\n');
    if (before !== await sourceDigest() || checked !== await key()) throw new Error('Sources changed during build; retry');
    const assets: Record<string,string> = {};
    for (const file of await files(stage)) assets[relative(stage,file).split('\\').join('/')] = digest(await fs.readFile(file));
    const generation = digest(JSON.stringify(assets));
    const target = join(output,'generations',generation);
    await fs.mkdir(join(output,'generations'),{recursive:true});
    try {await fs.rename(stage,target);} catch(error) {
      if (!['EEXIST','ENOTEMPTY'].includes((error as NodeJS.ErrnoException).code || '')) throw error;
      // A content-addressed directory may have been damaged after publication.
      // Repair individual files atomically; valid generations are never removed.
      for (const [name, hash] of Object.entries(assets)) {
        let valid = false;
        try {valid = digest(await fs.readFile(join(target, name))) === hash;} catch { /* missing asset */ }
        if (!valid) {
          await fs.mkdir(join(target, name, '..'), {recursive: true});
          await fs.rename(join(stage, name), join(target, name));
        }
      }
      await fs.rm(stage,{recursive:true,force:true});
    }
    const temporary = join(output,'.current-' + process.pid);
    const contract = digest(await fs.readFile(join(root,'oko/reader/generated/openapi.json')));
    await fs.writeFile(temporary,JSON.stringify({version:1,source:before,generation,contract,assets,check:checked},null,2)+'\n');
    await fs.rename(temporary,join(output,'current.json'));
    await checkAssets();
    await remember(root, 'frontend', checked);
  } finally {await fs.rm(stage,{recursive:true,force:true});}
}
if (process.argv.includes('--check')) {
  await checkAssets();
} else {
  await compile();
  if (process.argv.includes('--watch')) {
    let active = false, pending = false;
    const rebuild = async () => {
      pending = true; if(active) return; active=true;
      while(pending) {pending=false; try {await compile();} catch(error) {console.error(error instanceof Error ? error.message : String(error));}}
      active=false;
    };
    const watchDigest = () => fingerprint(root, [...inputs, 'api', 'scripts', 'oko/reader/generated',
      'oko/reader/legacy_generated', 'node_modules/.package-lock.json']);
    let seen = await watchDigest();
    // Poll content rather than relying on platform-specific recursive watchers.
    // This also observes atomic file replacements used by editors.
    setInterval(async () => {
      try {
        const next = await watchDigest();
        if(next !== seen) {seen=next; await rebuild();}
      } catch(error) {console.error(error instanceof Error ? error.message : String(error));}
    },250);
    console.log('Watching frontend; Python serves the latest complete generation.');
  }
}
