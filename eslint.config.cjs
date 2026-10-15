const globals = require('globals');

module.exports = [
  {files:['**/*.{js,cjs,mjs}'], rules:{'no-undef':'error'}},
  {files:['**/*.{js,mjs}'], languageOptions:{globals:globals.browser}},
  {files:['**/*.cjs'], languageOptions:{sourceType:'commonjs',globals:globals.node}},
  {files:['scripts/*.mjs','vite.config.mjs'], languageOptions:{globals:globals.node}},
];
