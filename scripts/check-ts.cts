// Parse exact TypeScript/TSX bytes; strict project types are checked separately.
import fs = require('node:fs');
import ts = require('typescript');
const stdin = process.argv[2] === '--stdin';
const batch = process.argv[2] === '--stdin-files';
const names = stdin ? process.argv.slice(3, 4) : process.argv.slice(2);
const inputs: {name: string; source: string}[] = batch ? JSON.parse(fs.readFileSync(0, 'utf8'))
  : names.map(name => ({name, source: fs.readFileSync(stdin ? 0 : name, 'utf8')}));
if (!inputs.length) throw new Error('Expected TypeScript inputs');
let failed = false;
for (const {name, source} of inputs) {
  const result = ts.transpileModule(source, {fileName: name, reportDiagnostics: true,
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX}});
  const diagnostics = result.diagnostics || [];
  process.stderr.write(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCurrentDirectory: () => process.cwd(), getCanonicalFileName: name => name, getNewLine: () => '\n',
  }));
  failed ||= diagnostics.some(diagnostic => diagnostic.category === ts.DiagnosticCategory.Error);
}
process.exitCode = failed ? 1 : 0;
