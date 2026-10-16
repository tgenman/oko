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
  navigation: '/api/navigation', cards: '/api/cards',
  material: '/api/materials/{item}', version: '/api/materials/{item}/versions/{number}', status: '/api/status',
  navigation_v3: '/api/v3/reading-composition', cards_v3: '/api/v3/cards', status_v3:'/api/v3/status',
  material_v3: '/api/v3/materials/{item}', version_v3: '/api/v3/materials/{item}/versions/{number}',
};
const loaded = new Set<string>();
const validators = new Map<string, ValidateFunction>();
function contractFor(url: string, method = 'GET') {
  if (method !== 'GET') return null;
  const pathname = new URL(url, 'http://localhost').pathname;
  if (pathname === '/api/v3/status') return 'status_v3';
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
  let check = validators.get(name);
  if (!check) {
    const route = routes[name];
    if (!route) throw new Error(`Unknown fixture contract: ${name}`);
    const library = name.endsWith('_v3') ? 'generated' : 'legacy_generated';
    if (!loaded.has(library)) {
      const document = JSON.parse(readFileSync(path.join(root, 'oko/reader', library, 'openapi.json'), 'utf8'));
      // OpenAPI metadata is not JSON Schema. Expose only schemas and the exact
      // response schemas, preserving their local #/components references.
      ajv.addSchema({components: document.components, responses: Object.fromEntries(
        Object.entries(routes).filter(([alias]) => alias.endsWith('_v3') === name.endsWith('_v3'))
          .map(([alias, url]) => [alias, document.paths[url].get.responses['200'].content['application/json'].schema]),
      )}, library);
      loaded.add(library);
    }
    check = ajv.compile({$ref: `${library}#/responses/${name}`});
    validators.set(name, check);
  }
  if (!check(body)) throw new Error(`${name} contract: ${ajv.errorsText(check.errors, {dataVar:'response'})}`);
}
export {contractFor, validate};
