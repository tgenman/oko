import {readFileSync} from 'node:fs';
import * as path from 'node:path';
import Ajv from 'ajv/dist/2020';
import type {ValidateFunction} from 'ajv';
import addFormats from 'ajv-formats';

// Read generated OpenAPI bytes directly: no Python startup or duplicate copies
// of components for each response. Compile only contracts used by this file.
const root = path.resolve(__dirname, '../..');
const ajv = new Ajv({allErrors:true,strict:false});
addFormats(ajv);
const routes: Record<string, string> = {
  status_details:'/api/v3/status/details',publication:'/api/v3/publication/{item}',
  navigation_subject: '/api/v3/navigation', navigation: '/api/v3/reading-composition', cards: '/api/v3/cards',
  material: '/api/v3/materials/{item}', status: '/api/v3/status',
  navigation_v3: '/api/v3/reading-composition', cards_v3: '/api/v3/cards', status_v3:'/api/v3/status',
  material_v3: '/api/v3/materials/{item}',
};
const loaded = new Set<string>();
const validators = new Map<string, ValidateFunction>();
function contractFor(url: string, method = 'GET') {
  if (method !== 'GET') return null;
  const pathname = new URL(url, 'http://localhost').pathname;
  if(/^\/api\/v3\/publication\/[^/]+$/.test(pathname))return 'publication';
  if (pathname === '/api/v3/navigation') return 'navigation_subject';
  if (pathname === '/api/v3/status/details') return 'status_details';
  if (pathname === '/api/v3/status') return 'status_v3';
  if (pathname === '/api/v3/cards') return 'cards_v3';
  if (pathname === '/api/v3/reading-composition') return 'navigation_v3';
  if (/^\/api\/v3\/materials\/[^/]+$/.test(pathname)) return 'material_v3';
  if (pathname === '/api/v3/cards') return 'cards';
  if (pathname === '/api/v3/reading-composition') return 'navigation';
  if (pathname === '/api/v3/status') return 'status';
  if (/^\/api\/materials\/[^/]+$/.test(pathname)) return 'material';
  return null;
}
function validate(name: string, body: unknown) {
  let check = validators.get(name);
  if (!check) {
    const route = routes[name];
    if (!route) throw new Error(`Unknown fixture contract: ${name}`);
    const library = 'generated';
    if (!loaded.has(library)) {
      const document = JSON.parse(readFileSync(path.join(root, 'oko/reader', library, 'openapi.json'), 'utf8'));
      // OpenAPI metadata is not JSON Schema. Expose only schemas and the exact
      // response schemas, preserving their local #/components references.
      ajv.addSchema({components: document.components, responses: Object.fromEntries(
        Object.entries(routes)
          .map(([alias, url]) => [alias, document.paths[url][alias==='reading_check'?'post':'get'].responses['200'].content['application/json'].schema]),
      )}, library);
      loaded.add(library);
    }
    check = ajv.compile({$ref: `${library}#/responses/${name}`});
    validators.set(name, check);
  }
  if (!check(body)) throw new Error(`${name} contract: ${ajv.errorsText(check.errors, {dataVar:'response'})}`);
}
export {contractFor, validate};
