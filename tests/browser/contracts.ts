import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
// Schemas are exported from the server's wire types, never handwritten here.
import * as path from 'node:path';
import Ajv from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
const root = path.resolve(__dirname, '../..');
const venv = path.join(root, '.venv/bin/python');
const schemas: Record<string, object> = JSON.parse(execFileSync(process.env.OKO_PYTHON || (existsSync(venv) ? venv : 'python3'), ['-m', 'oko.reader.contracts'], {cwd:root, encoding:'utf8'}));
const ajv = new Ajv({allErrors:true,strict:false});
addFormats(ajv);
const validators = Object.fromEntries(Object.entries(schemas).map(([name,schema]) => [name, ajv.compile(schema)]));
function contractFor(url: string, method = 'GET') {
  if (method !== 'GET') return null;
  const pathname = new URL(url, 'http://localhost').pathname;
  if (pathname === '/api/v3/cards') return 'cards_v3';
  if (pathname === '/api/v3/reading-composition') return 'navigation_v3';
  if (/^\/api\/v3\/materials\/[^/]+$/.test(pathname)) return 'material_v3';
  if (/^\/api\/v3\/materials\/[^/]+\/versions\/\d+$/.test(pathname)) return 'version_v3';
  if (pathname === '/api/cards') return 'cards';
  if (pathname === '/api/navigation') return 'navigation';
  if (pathname === '/api/status') return 'status';
  if (/^\/api\/materials\/[^/]+$/.test(pathname)) return 'material';
  if (/^\/api\/materials\/[^/]+\/versions\/\d+$/.test(pathname)) return 'version';
  return null;
}
function validate(name: string, body: unknown) {
  const check = validators[name];
  if (!check(body)) throw new Error(`${name} contract: ${ajv.errorsText(check.errors, {dataVar:'response'})}`);
}
export {contractFor, validate};
