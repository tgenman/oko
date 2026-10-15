import {test} from 'node:test';
import {spawn} from 'node:child_process';
import {newContext} from './browser-context.ts';
import {artifactPath} from './artifacts.ts';
import assert from 'node:assert/strict';
import * as path from 'node:path';

test('standalone Swagger displays the complete contract without reader calls or external assets', async () => {
  const server = spawn(process.env.OKO_PYTHON || path.resolve('.venv/bin/python'), ['scripts/api.py', 'docs', '--port', '0', '--assets', process.env.OKO_SWAGGER_ASSETS || path.resolve('node_modules/swagger-ui-dist')]);
  let output = '', stderr = '';
  server.stderr.on('data', value => stderr += value);
  const origin = await new Promise((resolve, reject) => {
    server.stdout.on('data', value => {
      output += value;
      const match = output.match(/http:\/\/127\.0\.0\.1:[0-9]+/);
      if (match) resolve(match[0]);
    });
    server.on('exit', code => reject(new Error(`Docs server exited ${code}: ${stderr}`)));
  });
  const context = await newContext();
  try {
    const page = await context.newPage();
    const requests: string[] = [], errors: string[] = [];
    page.on('request', request => requests.push(request.url()));
    page.on('pageerror', error => errors.push(error.message));
    const response = await page.goto(origin + '/docs');
    assert.match(response!.headers()['content-security-policy'], /script-src 'self'/);
    await page.locator('.opblock').last().waitFor();
    assert.equal(await page.locator('.opblock').count(), 50);
    await page.locator('.opblock-summary').filter({hasText: '/api/status'}).click();
    assert.equal(await page.getByRole('button', {name:'Try it out'}).count(), 0);
    assert.equal(await page.getByRole('button', {name:/Authorize/}).count(), 0);
    assert.deepEqual(errors, []);
    assert(requests.every(url => url.startsWith(origin + '/')));
    assert(!requests.some(url => new URL(url).pathname.startsWith('/api/')));
    const contract = await (await context.request.get(origin + '/openapi.json')).json();
    assert.equal(contract.openapi, '3.1.0');
    await page.screenshot({path:artifactPath('swagger-contract.png'), fullPage:true});
    assert.equal((await context.request.get(origin + '/api/status')).status(), 404);
  } finally {
    await context.close();
    server.kill('SIGTERM');
    await new Promise(resolve => server.once('exit', resolve));
  }
});
