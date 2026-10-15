// Node 20+ test runner preload. Project types are checked before execution.
// NodeNext preserves dynamic import() inside functions serialized by Playwright.
const fs = require('node:fs');
const ts = require('typescript');

require.extensions['.ts'] = (module, filename) => {
  const result = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename,
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.NodeNext,
      esModuleInterop: true, inlineSourceMap: true, inlineSources: true},
    reportDiagnostics: true,
  });
  const errors = result.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error);
  if (errors.length) throw new Error(ts.formatDiagnosticsWithColorAndContext(errors, {
    getCurrentDirectory: () => process.cwd(), getCanonicalFileName: name => name, getNewLine: () => '\n',
  }));
  module._compile(result.outputText, filename);
};
