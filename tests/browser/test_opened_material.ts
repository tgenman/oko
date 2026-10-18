import type {Page} from 'playwright';
import {test} from 'node:test';
import {reader, material, expect} from './support.ts';
import assert from 'node:assert/strict';

test('switch clears old material immediately and ignores late opening errors', async () => {
  const r = await reader();
  try {
    await r.start([material('A'), material('B'), material('C')]);
    await r.page.getByRole('button', {name: /^A/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.evaluate(() => { readerTransport.held.set('/api/v3/materials/B', true); readerTransport.held.set('/api/v3/materials/C', true); });
    await r.page.getByRole('button', {name: /^B/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/B'));
    await expect(r.page.locator('#text')).toHaveText('');
    await expect(r.page.locator('#empty')).toHaveText('Загрузка материала…');
    await r.page.getByRole('button', {name: /^C/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/C'));
    await r.page.evaluate(body => readerTransport.release('/api/v3/materials/C', body), material('C'));
    await expect(r.page.locator('#text')).toHaveText('Текст C');
    await r.page.evaluate(() => readerTransport.release('/api/v3/materials/B', {detail: 'Поздняя ошибка B'}, 500));
    await settle(r.page);
    await expect(r.page.locator('#text')).toHaveText('Текст C');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('late success of an old opening cannot replace the selected card', async () => {
  const r = await reader();
  try {
    await r.start([material('A'), material('B')]);
    await r.page.evaluate(() => readerTransport.held.set('/api/v3/materials/A', true));
    await r.page.getByRole('button', {name: /^A/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/A'));
    await r.page.getByRole('button', {name: /^B/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    await r.page.evaluate(body => readerTransport.release('/api/v3/materials/A', body), material('A'));
    await settle(r.page);
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

async function settle(page: Page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

test('previous version and media delivered after switching never reach the new card', async () => {
  const r = await reader();
  try {
    await r.start([material('A', {images: ['/api/v3/materials/A/images/image-1.png'], previous: [{url: '/api/v3/materials/A/versions/1'}]}), material('B')]);
    await r.page.evaluate(() => { readerTransport.held.set('/api/v3/materials/A/images/image-1.png', true); readerTransport.held.set('/api/v3/materials/A/versions/1', true); });
    await r.page.getByRole('button', {name: /^A/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/A/images/image-1.png'));
    await r.page.locator('#versions summary').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/A/versions/1'));
    await r.page.getByRole('button', {name: /^B/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    await r.page.evaluate(() => {
      readerTransport.release('/api/v3/materials/A/images/image-1.png', 'late image');
      readerTransport.release('/api/v3/materials/A/versions/1', {title: 'Прежнее A', text: 'СЕКРЕТ ПРЕЖНЕГО A', full:true, reason:'', images: [], attachments:[]});
    });
    await settle(r.page);
    await expect(r.page.locator('#material')).not.toContainText('СЕКРЕТ');
    await expect(r.page.locator('#images img')).toHaveCount(0);
    assert.deepEqual(await r.page.evaluate(() => browserResources.created), []);
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

async function videoFixture(page: Page) {
  return page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 32;
    const stream = canvas.captureStream(0);
    const recorder = new MediaRecorder(stream, {mimeType: 'video/webm;codecs=vp8'});
    const chunks: Blob[] = []; recorder.ondataavailable = event => chunks.push(event.data);
    const complete = new Promise(resolve => recorder.onstop = resolve);
    const started = new Promise(resolve => recorder.onstart = resolve);
    recorder.start();
    canvas.getContext('2d')!!.fillRect(0,0,32,32);
    (stream.getVideoTracks()[0] as CanvasCaptureMediaStreamTrack).requestFrame();
    await started;
    for (let frame = 0; frame < 8; frame++) {
      canvas.getContext('2d')!!.fillStyle = frame % 2 ? 'red' : 'blue';
      canvas.getContext('2d')!!.fillRect(0, 0, 32, 32);
      (stream.getVideoTracks()[0] as CanvasCaptureMediaStreamTrack).requestFrame();
      await new Promise(requestAnimationFrame);
    }
    recorder.stop(); await complete; stream.getTracks().forEach(track => track.stop());
    const bytes = new Uint8Array(await new Blob(chunks).arrayBuffer());
    return btoa(String.fromCharCode(...bytes));
  });
}
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1sAAAAASUVORK5CYII=';

test('viewing during media loading stops playback, releases URLs and rejects late media', async () => {
  const r = await reader();
  try {
    await r.start([material('A', {images: ['/api/v3/materials/A/images/image-2.png'], attachments: [
      {url: '/api/v3/materials/A/media/media-1.webm', kind: 'video', mime: 'video/webm'},
      {url: '/api/v3/materials/A/media/media-2.webm', kind: 'circle', mime: 'video/webm'},
    ]})]);
    const base64 = await videoFixture(r.page);
    await r.page.evaluate(({base64, png}) => {
      readerTransport.replies.set('/api/v3/materials/A/images/image-2.png', {base64: png, mime: 'image/png'});
      readerTransport.replies.set('/api/v3/materials/A/media/media-1.webm', {base64, mime: 'video/webm'});
      readerTransport.held.set('/api/v3/materials/A/media/media-2.webm', true);
    }, {base64, png});
    await r.page.getByRole('button', {name: /^A/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/A/media/media-2.webm'));
    await expect(r.page.locator('#images img')).toBeVisible();
    await expect(r.page.locator('#images video').first()).toHaveAttribute('src', /^blob:/);
    await r.page.waitForFunction(() => {
      const video = document.querySelector<HTMLVideoElement>('#images video')!;
      if (video?.error) throw new Error('Synthetic WebM decode failed: '+(video as HTMLVideoElement).error!.message);
      return video && (video as HTMLVideoElement).readyState >= 2;
    });
    await r.page.evaluate(async () => {
      window.testVideo = document.querySelector<HTMLVideoElement>('#images video')!;
      testVideo.muted = true; testVideo.loop = true; await testVideo.play();
    });
    await r.page.evaluate(() => readerTransport.replies.set('POST /api/v3/materials/A/viewed', {body:{ok:true},next:[['/api/v3/cards',{body:[]}]]}));
    await r.page.getByRole('button', {name: 'Просмотрено', exact: true}).click();
    await expect(r.page.locator('#material')).toBeHidden();
    assert.equal(await r.page.evaluate(() => testVideo.paused && !testVideo.hasAttribute('src')), true);
    assert.equal(await r.page.evaluate(() => browserResources.created.length), 2);
    assert.deepEqual(await r.page.evaluate(() => browserResources.revoked), await r.page.evaluate(() => browserResources.created));
    await r.page.evaluate(() => readerTransport.release('/api/v3/materials/A/media/media-2.webm', 'late circle'));
    await settle(r.page);
    await expect(r.page.locator('#images')).toBeEmpty();
    assert.equal(await r.page.evaluate(() => browserResources.created.length), 2);
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('viewing a publication in another tab closes its pending opening but preserves unrelated folder', async () => {
  const r = await reader();
  try {
    const source = material('source');
    const unrelated = material('other', {folder_id: 'other-folder'});
    await r.start([source, unrelated]);
    const otherTab = await r.context.newPage(); await r.start([source], otherTab);
    await r.page.evaluate(() => readerTransport.held.set('/api/v3/materials/source', true));
    await r.page.getByRole('button', {name: /^source/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/source'));
    await otherTab.getByRole('button', {name: /^source/}).click();
    await expect(otherTab.locator('#text')).toHaveText('Текст source');
    await otherTab.evaluate(() => readerTransport.replies.set('POST /api/v3/materials/source/viewed', {body:{ok:true},next:[['/api/v3/cards',{body:[]}]]}));
    await otherTab.getByRole('button', {name: 'Просмотрено', exact: true}).click();
    await expect(r.page.locator('#empty')).toHaveText('Выберите материал');
    await r.page.evaluate(body => readerTransport.release('/api/v3/materials/source', body), source);
    await settle(r.page);
    await expect(r.page.locator('#empty')).toHaveText('Выберите материал');
    await r.page.getByRole('button', {name: /^other/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст other');
    await otherTab.evaluate(() => { const channel = new BroadcastChannel('oko-reader'); channel.postMessage({kind: 'folder-viewed', folder: 'f'}); channel.close(); });
    await settle(r.page);
    await expect(r.page.locator('#text')).toHaveText('Текст other');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

for (const interruption of ['stop', 'connection loss']) {
  test(interruption + ' rejects a delayed material response', async () => {
    const r = await reader();
    try {
      await r.start([material('A')]);
      await r.page.evaluate(() => readerTransport.held.set('/api/v3/materials/A', true));
      await r.page.getByRole('button', {name: /^A/}).click();
      await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/A'));
      if (interruption === 'stop') await r.page.getByRole('button', {name: 'Остановить Око'}).click();
      else {
        await r.page.evaluate(() => readerTransport.replies.set('/api/v3/status', {status: 503, body: {detail: 'Связь потеряна'}}));
      }
      await expect(r.page.locator('#empty')).toHaveText('Выберите материал');
      await r.page.evaluate(body => readerTransport.release('/api/v3/materials/A', body), material('A'));
      await settle(r.page);
      await expect(r.page.locator('#text')).toHaveText('');
      await expect(r.page.locator('#material')).toBeHidden();
      assert.deepEqual(r.errors, []);
    } finally {await r.close();}
  });
}

test('title opens original in a new tab and resets for other cards', async () => {
  const r = await reader();
  try {
    await r.context.route('https://example.org/**', route => route.fulfill({body: '<h1>Original</h1>', contentType: 'text/html'}));
    await r.start([material('A'), material('B')]);
    await r.page.getByRole('button', {name: /^A/}).click();
    const link = r.page.locator('#title a');
    await expect(link).toHaveAttribute('href', 'https://example.org/A');
    await expect(link).toHaveText('A');
    await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    const popupPromise = r.page.waitForEvent('popup');
    await link.click();
    const popup = await popupPromise;
    await popup.waitForLoadState();
    assert.equal(popup.url(), 'https://example.org/A');
    assert.equal(await popup.evaluate(() => window.opener === null), true);
    await popup.close();
    await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.getByRole('button', {name: /^B/}).click();
    await expect(r.page.locator('#title')).toHaveText('B');
    await expect(link).toHaveAttribute('href', 'https://example.org/B');
    assert.deepEqual(r.errors, []);
  } finally { await r.close(); }
});

test('background processing updates keep long material visible and preserve scroll while waiting', async () => {
 const r=await reader();try {
  await r.start([material('long',{text:'Длинный текст\n'.repeat(500)})]);
  await r.page.locator('[data-card="long"]').click();
  await expect(r.page.locator('#text')).toContainText('Длинный текст');
  await r.page.locator('article').evaluate(node=>node.scrollTop=700);
  const before=await r.page.locator('article').evaluate(node=>node.scrollTop);expect(before).toBeGreaterThan(100);
  await r.page.evaluate(()=>{const t=readerTransport;t.held.set('/api/v3/materials/long',true);t.replies.get('/api/v3/cards')!.body![0].ai={status:'pending',error:'Запрещена публичная AI-обработка'};});
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/materials/long'));
  await expect(r.page.locator('#text')).toContainText('Длинный текст');
  expect(await r.page.locator('article').evaluate(node=>node.scrollTop)).toBe(before);
  await r.page.evaluate(()=>readerTransport.release('/api/v3/materials/long',readerTransport.replies.get('/api/v3/materials/long')!.body));
  await expect(r.page.locator('#text')).toContainText('Длинный текст');
  expect(await r.page.locator('article').evaluate(node=>node.scrollTop)).toBe(before);
 }finally{await r.close();}
});
