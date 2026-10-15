import type {Linter} from 'eslint';
import globals from 'globals';

export default [
  {files:['**/*.{js,cjs,mjs}'], rules:{'no-undef':'error'}},
  {files:['**/*.{js,mjs}'], languageOptions:{globals:globals.browser}},
  {files:['**/*.cjs'], languageOptions:{sourceType:'commonjs',globals:globals.node}},
  {files:['scripts/*.mjs','vite.config.mjs'], languageOptions:{globals:globals.node}},
  // lint-js.cts supplies erased executable code under virtual JS filenames.
  {files:['tests/browser/**/*.mjs'], languageOptions:{globals:{...globals.browser,...globals.node,readerTransport:'readonly',browserResources:'readonly',testVideo:'readonly'}}},
] satisfies Linter.Config[];
