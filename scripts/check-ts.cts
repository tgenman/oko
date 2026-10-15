// Parse exact TypeScript/TSX bytes; strict project types are checked separately.
import fs = require('node:fs');
import ts = require('typescript');
const stdin = process.argv[2] === '--stdin';
const name = process.argv[stdin ? 3 : 2];
if (!name) throw new Error('Expected a TypeScript filename');
const source = fs.readFileSync(stdin ? 0 : name,'utf8');
const result = ts.transpileModule(source, {fileName:name,reportDiagnostics:true,
  compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}});
const diagnostics = result.diagnostics || [];
process.stderr.write(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
  getCurrentDirectory:()=>process.cwd(),getCanonicalFileName:name=>name,getNewLine:()=> '\n',
}));
process.exitCode = diagnostics.some(diagnostic=>diagnostic.category===ts.DiagnosticCategory.Error) ? 1 : 0;
