import {test} from 'node:test';
import {newContext} from './browser-context.ts';
import assert from 'node:assert/strict';

test('shared Chromium isolates storage and closing one scenario leaves another usable', async t => {
  const first = await newContext(), second = await newContext();
  t.after(async () => {await first.close(); await second.close();});
  assert.equal(first.browser(), second.browser());
  for (const context of [first, second]) {
    await context.route('http://isolation.test/**', route => route.fulfill({contentType: 'text/html', body: '<title>Fixture</title>'}));
  }
  const a = await first.newPage(), b = await second.newPage();
  await a.goto('http://isolation.test/');
  await a.evaluate(() => {localStorage.setItem('draft', 'old'); document.cookie = 'session=old';});
  await b.goto('http://isolation.test/');
  assert.deepEqual(await b.evaluate(() => [localStorage.getItem('draft'), document.cookie]), [null, '']);
  const popup = await first.newPage();
  await first.close();
  assert.ok(a.isClosed() && popup.isClosed());
  assert.equal(await b.title(), 'Fixture');
  const third = await newContext();
  t.after(() => third.close());
  assert.equal(third.browser(), second.browser());
  assert.deepEqual(await third.cookies(), []);
});

test('ordinary missing UI controls fail promptly without waiting thirty seconds', {timeout:15000}, async t => {
  const context=await newContext();t.after(()=>context.close());
  const page=await context.newPage();await page.setContent('<button>Existing</button>');
  const started=Date.now();
  await assert.rejects(page.getByRole('button',{name:'Missing',exact:true}).click(),/Timeout 5000ms exceeded/);
  assert.ok(Date.now()-started<10000,'Ordinary UI action waited too long');
});
