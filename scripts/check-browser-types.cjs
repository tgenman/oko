// Check exact staged test/source bytes; unstaged fixes cannot hide a bad commit.
const {execFileSync, spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const git = (...args) => execFileSync('git', args);
const changed = git('diff', '--cached', '--name-only', '-z').toString().split('\0');
const input = name => name === 'tsconfig.browser-tests.json' || name === 'tsconfig.json'
  || name === 'package.json' || name === 'package-lock.json'
  || /^(tests\/browser|frontend)\/.*\.(ts|tsx)$/.test(name);
if (changed.some(input)) {
  const entries = git('ls-files', '--stage', '-z').toString().split('\0').filter(Boolean);
  if (entries.some(entry => entry.endsWith('\ttsconfig.browser-tests.json'))) {
    const root = git('rev-parse', '--show-toplevel').toString().trim();
    const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'oko-browser-index-'));
    try {
      for (const entry of entries) {
        const [metadata, name] = entry.split('\t');
        if (!input(name)) continue;
        const [mode, identity, index] = metadata.split(' ');
        if (index !== '0' || !['100644', '100755'].includes(mode)) throw new Error('Unsupported staged input: ' + name);
        const target = path.join(stage, name);
        fs.mkdirSync(path.dirname(target), {recursive: true});
        fs.writeFileSync(target, git('cat-file', 'blob', identity));
      }
      fs.symlinkSync(path.join(root, 'node_modules'), path.join(stage, 'node_modules'), 'dir');
      console.log('CHECK exact staged browser TypeScript');
      const result = spawnSync(process.execPath, [path.join(root, 'node_modules/typescript/bin/tsc'),
        '-p', path.join(stage, 'tsconfig.browser-tests.json')], {cwd: stage, stdio: 'inherit'});
      if (result.error) throw result.error;
      process.exitCode = result.status ?? 1;
    } finally {
      fs.rmSync(stage, {recursive: true, force: true});
    }
  }
}
