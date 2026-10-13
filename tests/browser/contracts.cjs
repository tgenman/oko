// Schemas are exported from the server's wire types, never handwritten here.
const {execFileSync} = require('node:child_process');
const {existsSync} = require('node:fs');
const path = require('node:path');
const Ajv = require('ajv');
const root = path.resolve(__dirname, '../..');
const venv = path.join(root, '.venv/bin/python');
const schemas = JSON.parse(execFileSync(process.env.OKO_PYTHON || (existsSync(venv) ? venv : 'python3'), ['-m', 'oko.reader.contracts'], {cwd:root, encoding:'utf8'}));
const ajv = new Ajv({allErrors:true});
const validators = Object.fromEntries(Object.entries(schemas).map(([name,schema]) => [name, ajv.compile(schema)]));
function contractFor(url, method = 'GET') {
  if (method !== 'GET') return null;
  const pathname = new URL(url, 'http://localhost').pathname;
  if (pathname === '/api/cards') return 'cards';
  if (pathname === '/api/navigation') return 'navigation';
  if (pathname === '/api/status') return 'status';
  if (/^\/api\/materials\/[^/]+$/.test(pathname)) return 'material';
  if (/^\/api\/materials\/[^/]+\/versions\/\d+$/.test(pathname)) return 'version';
  return null;
}
function validate(name, body) {
  const check = validators[name];
  if (!check(body)) throw new Error(`${name} contract: ${ajv.errorsText(check.errors, {dataVar:'response'})}`);
}
module.exports = {contractFor, validate};
