const globals = require('globals');

module.exports = [
  {files:['**/*.{js,cjs,mjs}'], rules:{'no-undef':'error'}},
  {files:['**/*.{js,mjs}'], languageOptions:{globals:globals.browser}},
  {files:['**/*.cjs'], languageOptions:{sourceType:'commonjs',globals:globals.node}},
  {files:['tests/browser/**/*.cjs'], languageOptions:{globals:{...globals.browser, readerTransport:'readonly', browserResources:'readonly', testVideo:'readonly'}}},
];
