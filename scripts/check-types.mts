// Successful type checks are reusable only for the same source bytes and toolchain.
import {execFileSync} from 'node:child_process';
import ts from 'typescript';
import {resolve, join, relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {cached, fingerprint, remember, toolchain} from './api-cache.mts';

export async function checkTypes(root: string, config: string) {
  process.env.NODE_ENV ??= 'production';
  const inputKey = async () => {
    const configs: string[] = [];
    const parsed = ts.getParsedCommandLineOfConfigFile(join(root, config), {}, {
      ...ts.sys,
      readFile: file => { configs.push(relative(root, file)); return ts.sys.readFile(file); },
      onUnRecoverableConfigFileDiagnostic: diagnostic => { throw new Error(ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')); },
    });
    if (!parsed || parsed.errors.length) throw new Error('Invalid TypeScript project: ' + config);
    // Include arbitrary tracked imported files/configs, not only today's directory layout.
    let inventory: string[] = [];
    try {
      inventory = execFileSync('git', ['ls-files', '-co', '--exclude-standard', '-z'],
        {cwd: root, stdio: ['ignore', 'pipe', 'ignore']}).toString().split('\0').filter(name => /\.(tsx?|[cm]ts|jsx?|[cm]js|json)$/.test(name));
    } catch { /* Standalone synthetic builder fixtures have no Git repository. */ }
    return fingerprint(root, [...new Set(['frontend', 'scripts', 'tests/browser',
      'tsconfig.json', 'tsconfig.tooling.json', 'tsconfig.browser-tests.json',
      'eslint.config.ts', 'vite.config.mts', ...configs, ...inventory,
      ...parsed.fileNames.map(file => relative(root, file))])].sort(), await toolchain(root));
  };
  const before = await inputKey(), name = 'types-' + config;
  if (await cached(root, name, before) && before === await inputKey()) {
    console.log('Type check cache hit: ' + config);
    return;
  }
  execFileSync(process.execPath, [join(root, 'node_modules/typescript/bin/tsc'), '-p', join(root, config)],
    {cwd: root, stdio: 'inherit'});
  if (before !== await inputKey()) throw new Error('Type check inputs changed while running');
  await remember(root, name, before);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const config = process.argv[2];
  if (!config || !['tsconfig.json', 'tsconfig.tooling.json', 'tsconfig.browser-tests.json'].includes(config)) {
    throw new Error('Expected a project tsconfig');
  }
  await checkTypes(resolve(import.meta.dirname, '..'), config);
}
