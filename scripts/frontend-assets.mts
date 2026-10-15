import {createHash} from 'node:crypto';
import {promises as fs} from 'node:fs';
import {resolve} from 'node:path';

const digest = (bytes: string | Buffer) => createHash('sha256').update(bytes).digest('hex');

// Validate the published generation, including its association with checked inputs.
export async function validateAssets(output: string, source: string, contract: string, expected?: string) {
  const manifest = JSON.parse(await fs.readFile(resolve(output, 'current.json'), 'utf8'));
  if (manifest.source !== source) throw new Error('Frontend build is stale. Run npm run build');
  if (!/^[a-f0-9]{64}$/.test(manifest.generation) || !manifest.assets?.['index.html']) throw new Error('Invalid frontend manifest');
  if (manifest.generation !== digest(JSON.stringify(manifest.assets))) throw new Error('Frontend asset inventory changed');
  if (expected && manifest.check !== expected) throw new Error('Frontend build inputs changed');
  const directory = resolve(output, 'generations', manifest.generation);
  for (const [name, hash] of Object.entries(manifest.assets)) {
    const file = await fs.realpath(resolve(directory, name));
    const realDirectory = await fs.realpath(directory);
    if (!file.startsWith(realDirectory + '/') || digest(await fs.readFile(file)) !== hash) throw new Error('Frontend asset corrupt: ' + name);
  }
  if (manifest.contract !== contract) throw new Error('Mixed API contract');
}
