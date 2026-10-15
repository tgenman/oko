import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

let directory: string | undefined;
export function artifactPath(name: string) {
  if (!name || path.basename(name) !== name || ['.','..'].includes(name)) throw new Error('Artifact name must be a filename');
  directory ??= process.env.OKO_TEST_ARTIFACT_DIR
    ? path.join(path.resolve(process.env.OKO_TEST_ARTIFACT_DIR), String(process.pid))
    : fs.mkdtempSync(path.join(os.tmpdir(), 'oko-browser-artifacts-'));
  fs.mkdirSync(directory, {recursive:true});
  return path.join(directory, name);
};
