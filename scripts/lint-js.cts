import {ESLint} from 'eslint';
import fs = require('node:fs');
import path = require('node:path');
import ts = require('typescript');

async function main() {
  const root = path.resolve(__dirname, '..');
  const eslint = new ESLint({cwd: root, overrideConfigFile: path.join(root, 'eslint.config.ts')});
  const args = process.argv.slice(2);
  const diagnosticsHost: ts.FormatDiagnosticsHost = {
    getCurrentDirectory: () => root, getCanonicalFileName: name => name, getNewLine: () => '\n',
  };
  async function lintSource(source: string, name: string) {
    if (/\.(?:[cm]?ts|tsx)$/.test(name)) {
      // Run the existing no-undef check on executable code, excluding erased
      // type names. Strict TypeScript separately checks the original sources.
      const compiled = ts.transpileModule(source, {fileName: name, reportDiagnostics: true,
        compilerOptions: {target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
          module: name.endsWith('.cts') ? ts.ModuleKind.CommonJS : ts.ModuleKind.ESNext}});
      const errors = (compiled.diagnostics || []).filter(d => d.category === ts.DiagnosticCategory.Error);
      if (errors.length) throw new Error(ts.formatDiagnosticsWithColorAndContext(errors, diagnosticsHost));
      return eslint.lintText(compiled.outputText, {filePath: name.replace(/\.(?:[cm]?ts|tsx)$/, name.endsWith('.cts') ? '.cjs' : '.mjs')});
    }
    return eslint.lintText(source, {filePath: name});
  }
  let results: ESLint.LintResult[] = [];
  if (args[0] === '--stdin') {
    if (!args[1]) throw new Error('Expected stdin filename');
    results = await lintSource(fs.readFileSync(0, 'utf8'), args[1]);
  } else {
    const defaults = [
      ...fs.readdirSync(path.join(root, 'scripts')).filter(name => /\.(?:[cm]?ts|tsx)$/.test(name)).map(name => path.join(root, 'scripts', name)),
      path.join(root, 'eslint.config.ts'), path.join(root, 'vite.config.mts'),
    ];
    for (const name of args.length ? args : defaults) {
      results.push(...await lintSource(fs.readFileSync(name, 'utf8'), name));
    }
  }
  const formatter = await eslint.loadFormatter('stylish');
  process.stdout.write(await formatter.format(results));
  process.exitCode = results.some(result => result.errorCount || result.warningCount) ? 1 : 0;
}
main().catch((error: unknown) => {console.error(error instanceof Error ? error.message : String(error)); process.exitCode = 1;});
