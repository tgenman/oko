// Local successful-check receipts. Keys describe bytes, never mtimes or a skip flag.
import {createHash} from 'node:crypto';
import {promises as fs} from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';

export const cacheDirectory = (root: string) => path.join(root, '.scratch/check-cache');

export async function fingerprint(root: string, inputs: string[], extra: unknown = null) {
  const hash = createHash('sha256').update(JSON.stringify({version: 1, root, extra}));
  async function visit(name: string) {
    const file = path.join(root, name);
    hash.update(name).update('\0');
    let stat;
    try {stat = await fs.stat(file);} catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      hash.update('missing\0'); return;
    }
    if (stat.isDirectory()) {
      hash.update('directory\0');
      for (const entry of (await fs.readdir(file)).sort()) {
        if (entry !== '__pycache__' && !entry.endsWith('.pyc')) await visit(path.join(name, entry));
      }
    } else {
      hash.update('file\0').update(await fs.readFile(file)).update('\0');
    }
  }
  for (const name of inputs) await visit(name);
  return hash.digest('hex');
}

export async function toolchain(root: string, python?: string) {
  const packageFile = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
  const packages = Object.keys({...packageFile.dependencies, ...packageFile.devDependencies}).sort();
  const node = await fingerprint(root, ['package.json', 'package-lock.json', 'node_modules/.package-lock.json',
    ...packages.map(name => 'node_modules/' + name + '/package.json')], {version: process.version, executable: process.execPath,
    environment: Object.fromEntries(Object.entries(process.env)
      .filter(([name]) => !name.startsWith('npm_') && !['PWD', 'OLDPWD', 'SHLVL', '_', 'INIT_CWD', 'OKO_TEST_ARTIFACT_DIR'].includes(name))
      .sort(([a], [b]) => a.localeCompare(b)))});
  const environment = python ? execFileSync(python, ['-c',
    'import importlib.metadata,json,sys; print(json.dumps([sys.version,sys.executable,sys.prefix,sorted((d.metadata["Name"],d.version) for d in importlib.metadata.distributions())]))'],
    {encoding: 'utf8', cwd: root}) : null;
  return {node, python: environment, pythonPath: process.env.PYTHONPATH};
}

export async function cached(root: string, name: string, key: string) {
  try {
    const value = JSON.parse(await fs.readFile(path.join(cacheDirectory(root), name, key + '.json'), 'utf8'));
    return value.version === 1 && value.key === key;
  } catch {return false;}
}

export async function remember(root: string, name: string, key: string) {
  const directory = path.join(cacheDirectory(root), name);
  await fs.mkdir(directory, {recursive: true});
  const temporary = path.join(directory, key + '.' + process.pid + '.tmp');
  await fs.writeFile(temporary, JSON.stringify({version: 1, key}) + '\n');
  await fs.rename(temporary, path.join(directory, key + '.json'));
}
