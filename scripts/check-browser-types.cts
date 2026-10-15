// Check exact staged test/source bytes; unstaged fixes cannot hide a bad commit.
import {execFileSync, spawnSync} from 'node:child_process';
import fs = require('node:fs');
import os = require('node:os');
import path = require('node:path');

const git = (...args: string[]) => execFileSync('git', args);
const changed = git('diff', '--cached', '--name-only', '-z').toString().split('\0');
const input = (name: string) => name === 'tsconfig.browser-tests.json' || name === 'tsconfig.json' || name === 'tsconfig.tooling.json' || name === 'eslint.config.ts' || name === 'vite.config.mts'
  || name === 'package.json' || name === 'package-lock.json'
  || /^(tests\/browser|frontend|scripts)\/.*\.(ts|tsx|cts|mts)$/.test(name);
if (changed.some(input)) {
  const entries = git('ls-files', '--stage', '-z').toString().split('\0').filter(Boolean);
  const configs = ['tsconfig.browser-tests.json', 'tsconfig.tooling.json'].filter(name => entries.some(entry => entry.endsWith('\t' + name)));
  if (configs.length) {
    const root = git('rev-parse', '--show-toplevel').toString().trim();
    const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'oko-browser-index-'));
    try {
      for (const entry of entries) {
        const [metadata, name] = entry.split('\t');
        if (!name || !input(name)) continue;
        const [mode, identity, index] = metadata!.split(' ');
        if (index !== '0' || !mode || !identity || !['100644', '100755'].includes(mode)) throw new Error('Unsupported staged input: ' + name);
        const target = path.join(stage, name);
        fs.mkdirSync(path.dirname(target), {recursive: true});
        fs.writeFileSync(target, git('cat-file', 'blob', identity));
      }
      fs.symlinkSync(path.join(root, 'node_modules'), path.join(stage, 'node_modules'), 'dir');
      for (const config of configs) {
        console.log('CHECK exact staged TypeScript: ' + config);
        const result = spawnSync(process.execPath, [path.join(root, 'node_modules/typescript/bin/tsc'),
          '-p', path.join(stage, config)], {cwd: stage, stdio: 'inherit'});
        if (result.error) throw result.error;
        if (result.status !== 0) process.exitCode = result.status ?? 1;
      }
    } finally {
      fs.rmSync(stage, {recursive: true, force: true});
    }
  }
}
