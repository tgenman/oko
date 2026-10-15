// Type-check exact Git index bytes; determine affected projects from their import graphs.
import {execFileSync} from 'node:child_process';
import fs = require('node:fs');
import os = require('node:os');
import path = require('node:path');
import ts = require('typescript');

const git = (...args: string[]) => execFileSync('git', args);
const changed = git('diff', '--cached', '--name-only', '--no-renames', '-z').toString().split('\0').filter(Boolean);
const input = (name: string) => /\.(ts|tsx|cts|mts|js|jsx|mjs|cjs|json)$/.test(name);
if (changed.some(input)) {
  const entries = git('ls-files', '--stage', '-z').toString().split('\0').filter(Boolean);
  const configs = ['tsconfig.browser-tests.json', 'tsconfig.tooling.json'].filter(name => entries.some(entry => entry.endsWith('\t' + name)));
  if (configs.length) {
    const root = git('rev-parse', '--show-toplevel').toString().trim();
    const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'oko-browser-index-'));
    try {
      const names: string[] = [], identities: string[] = [];
      for (const entry of entries) {
        const [metadata, name] = entry.split('\t');
        if (!name || !input(name)) continue;
        const [mode, identity, index] = metadata!.split(' ');
        if (index !== '0' || !identity || !mode || !['100644', '100755'].includes(mode)) throw new Error('Unsupported staged input: ' + name);
        names.push(name);
        identities.push(identity);
      }
      // Raw blobs avoid checkout-index smudge/eol filters changing staged bytes.
      const blobs = execFileSync('git', ['cat-file', '--batch'],
        {input: identities.join('\n') + '\n', maxBuffer: 64 * 1024 * 1024});
      let offset = 0;
      for (const [index, name] of names.entries()) {
        const end = blobs.indexOf(10, offset);
        const [identity, kind, length] = blobs.subarray(offset, end).toString().split(' ');
        const size = Number(length);
        if (end < offset || identity !== identities[index] || kind !== 'blob' || !Number.isSafeInteger(size)
            || size < 0 || end + 1 + size >= blobs.length) throw new Error('Invalid staged blob: ' + name);
        const target = path.join(stage, name);
        fs.mkdirSync(path.dirname(target), {recursive: true});
        fs.writeFileSync(target, blobs.subarray(end + 1, end + 1 + size));
        offset = end + 2 + size;
      }
      fs.symlinkSync(path.join(root, 'node_modules'), path.join(stage, 'node_modules'), 'dir');
      const globalChange = changed.some(name => name.endsWith('.json')
        || input(name) && !names.includes(name)); // Deletion can remove a graph edge.
      for (const config of configs) {
        const configPath = path.join(stage, config);
        const parsed = ts.getParsedCommandLineOfConfigFile(configPath, {noEmit: true}, {
          ...ts.sys, onUnRecoverableConfigFileDiagnostic: diagnostic => { throw new Error(ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')); },
        });
        if (!parsed) throw new Error('Cannot parse ' + config);
        const program = ts.createProgram({rootNames: parsed.fileNames, options: parsed.options, projectReferences: parsed.projectReferences});
        const sources = new Set(program.getSourceFiles().map(file => path.relative(stage, file.fileName).split(path.sep).join('/')));
        if (!globalChange && !parsed.errors.length && !changed.some(name => sources.has(name))) continue;
        console.log('CHECK exact staged TypeScript: ' + config);
        const diagnostics = [...parsed.errors, ...ts.getPreEmitDiagnostics(program)];
        process.stderr.write(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
          getCurrentDirectory: () => stage, getCanonicalFileName: name => name, getNewLine: () => '\n',
        }));
        if (diagnostics.some(item => item.category === ts.DiagnosticCategory.Error)) process.exitCode = 1;
      }
    } finally {
      fs.rmSync(stage, {recursive: true, force: true});
    }
  }
}
