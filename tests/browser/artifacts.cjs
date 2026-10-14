const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

let directory;
exports.artifactPath = function(name) {
  if (!name || path.basename(name) !== name || ['.','..'].includes(name)) throw new Error('Artifact name must be a filename');
  directory ??= process.env.OKO_TEST_ARTIFACT_DIR
    ? path.join(path.resolve(process.env.OKO_TEST_ARTIFACT_DIR), String(process.pid))
    : fs.mkdtempSync(path.join(os.tmpdir(), 'oko-browser-artifacts-'));
  fs.mkdirSync(directory, {recursive:true});
  return path.join(directory, name);
};
