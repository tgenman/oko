const {test} = require('node:test');
const assert = require('node:assert/strict');
const {reader, material, expect} = require('./support.cjs');

test('switch clears old material immediately and ignores late opening errors', async () => {
  const r = await reader();
  try {
    await r.start([material('A'), material('B'), material('C')]);
    await r.page.getByRole('button', {name: /^A/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.evaluate(() => { readerTransport.held.set('/api/materials/B', true); readerTransport.held.set('/api/materials/C', true); });
    await r.page.getByRole('button', {name: /^B/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/materials/B'));
    await expect(r.page.locator('#text')).toHaveText('');
    await expect(r.page.locator('#empty')).toHaveText('Загрузка материала…');
    await r.page.getByRole('button', {name: /^C/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/materials/C'));
    await r.page.evaluate(body => readerTransport.release('/api/materials/C', body), material('C'));
    await expect(r.page.locator('#text')).toHaveText('Текст C');
    await r.page.evaluate(() => readerTransport.release('/api/materials/B', {detail: 'Поздняя ошибка B'}, 500));
    await settle(r.page);
    await expect(r.page.locator('#text')).toHaveText('Текст C');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('late success of an old opening cannot replace the selected card', async () => {
  const r = await reader();
  try {
    await r.start([material('A'), material('B')]);
    await r.page.evaluate(() => readerTransport.held.set('/api/materials/A', true));
    await r.page.getByRole('button', {name: /^A/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/materials/A'));
    await r.page.getByRole('button', {name: /^B/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    await r.page.evaluate(body => readerTransport.release('/api/materials/A', body), material('A'));
    await settle(r.page);
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

async function settle(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

test('previous version and media delivered after switching never reach the new card', async () => {
  const r = await reader();
  try {
    await r.start([material('A', {images: ['/api/image-A'], previous: [{url: '/api/materials/A/versions/1'}]}), material('B')]);
    await r.page.evaluate(() => { readerTransport.held.set('/api/image-A', true); readerTransport.held.set('/api/materials/A/versions/1', true); });
    await r.page.getByRole('button', {name: /^A/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/image-A'));
    await r.page.locator('#versions summary').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/materials/A/versions/1'));
    await r.page.getByRole('button', {name: /^B/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    await r.page.evaluate(() => {
      readerTransport.release('/api/image-A', 'late image');
      readerTransport.release('/api/materials/A/versions/1', {title: 'Прежнее A', text: 'СЕКРЕТ ПРЕЖНЕГО A', full:true, reason:'', images: [], attachments:[]});
    });
    await settle(r.page);
    await expect(r.page.locator('#material')).not.toContainText('СЕКРЕТ');
    await expect(r.page.locator('#images img')).toHaveCount(0);
    assert.deepEqual(await r.page.evaluate(() => browserResources.created), []);
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

async function videoFixture(page) {
  return page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 32;
    const stream = canvas.captureStream(0);
    const recorder = new MediaRecorder(stream, {mimeType: 'video/webm;codecs=vp8'});
    const chunks = []; recorder.ondataavailable = event => chunks.push(event.data);
    const complete = new Promise(resolve => recorder.onstop = resolve);
    const started = new Promise(resolve => recorder.onstart = resolve);
    recorder.start();
    canvas.getContext('2d').fillRect(0,0,32,32);
    stream.getVideoTracks()[0].requestFrame();
    await started;
    for (let frame = 0; frame < 8; frame++) {
      canvas.getContext('2d').fillStyle = frame % 2 ? 'red' : 'blue';
      canvas.getContext('2d').fillRect(0, 0, 32, 32);
      stream.getVideoTracks()[0].requestFrame();
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
    await r.start([material('A', {images: ['/api/image'], attachments: [
      {url: '/api/video', kind: 'video', mime: 'video/webm'},
      {url: '/api/circle', kind: 'circle', mime: 'video/webm'},
    ]})]);
    const base64 = await videoFixture(r.page);
    await r.page.evaluate(({base64, png}) => {
      readerTransport.replies.set('/api/image', {base64: png, mime: 'image/png'});
      readerTransport.replies.set('/api/video', {base64, mime: 'video/webm'});
      readerTransport.held.set('/api/circle', true);
    }, {base64, png});
    await r.page.getByRole('button', {name: /^A/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/circle'));
    await expect(r.page.locator('#images img')).toBeVisible();
    await expect(r.page.locator('#images video').first()).toHaveAttribute('src', /^blob:/);
    await r.page.waitForFunction(() => {
      const video = document.querySelector('#images video');
      if (video?.error) throw new Error('Synthetic WebM decode failed: '+video.error.message);
      return video && video.readyState >= 2;
    });
    await r.page.evaluate(async () => {
      window.testVideo = document.querySelector('#images video');
      testVideo.muted = true; testVideo.loop = true; await testVideo.play();
    });
    await r.page.evaluate(() => readerTransport.replies.set('POST /api/materials/A/viewed', {body:{ok:true},next:[['/api/cards',{body:[]}]]}));
    await r.page.getByRole('button', {name: 'Просмотрено', exact: true}).click();
    await expect(r.page.locator('#material')).toBeHidden();
    assert.equal(await r.page.evaluate(() => testVideo.paused && !testVideo.hasAttribute('src')), true);
    assert.equal(await r.page.evaluate(() => browserResources.created.length), 2);
    assert.deepEqual(await r.page.evaluate(() => browserResources.revoked), await r.page.evaluate(() => browserResources.created));
    await r.page.evaluate(() => readerTransport.release('/api/circle', 'late circle'));
    await settle(r.page);
    await expect(r.page.locator('#images')).toBeEmpty();
    assert.equal(await r.page.evaluate(() => browserResources.created.length), 2);
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('viewing a source in another tab closes its pending digest but preserves unrelated folder', async () => {
  const r = await reader();
  try {
    const source = material('source');
    const digest = material('digest', {sources: [source], digest: {points: []}});
    const unrelated = material('other', {folder_id: 'other-folder'});
    await r.start([digest, unrelated]);
    const otherTab = await r.context.newPage(); await r.start([source], otherTab);
    await r.page.evaluate(() => readerTransport.held.set('/api/materials/digest', true));
    await r.page.getByRole('button', {name: /^Обзор/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/materials/digest'));
    await otherTab.getByRole('button', {name: /^source/}).click();
    await expect(otherTab.locator('#text')).toHaveText('Текст source');
    await otherTab.evaluate(() => readerTransport.replies.set('POST /api/materials/source/viewed', {body:{ok:true},next:[['/api/cards',{body:[]}]]}));
    await otherTab.getByRole('button', {name: 'Просмотрено', exact: true}).click();
    await expect(r.page.locator('#empty')).toHaveText('Выберите материал');
    await r.page.evaluate(body => readerTransport.release('/api/materials/digest', body), digest);
    await settle(r.page);
    await expect(r.page.locator('#event-sources')).toBeEmpty();
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
      await r.page.evaluate(() => readerTransport.held.set('/api/materials/A', true));
      await r.page.getByRole('button', {name: /^A/}).click();
      await r.page.waitForFunction(() => readerTransport.pending.has('/api/materials/A'));
      if (interruption === 'stop') await r.page.getByRole('button', {name: 'Остановить Око'}).click();
      else {
        await r.page.evaluate(() => readerTransport.replies.set('/api/status', {status: 503, body: {detail: 'Связь потеряна'}}));
      }
      await expect(r.page.locator('#empty')).toHaveText('Выберите материал');
      await r.page.evaluate(body => readerTransport.release('/api/materials/A', body), material('A'));
      await settle(r.page);
      await expect(r.page.locator('#text')).toHaveText('');
      await expect(r.page.locator('#material')).toBeHidden();
      assert.deepEqual(r.errors, []);
    } finally {await r.close();}
  });
}

test('digest originals and previous versions retain images, video and circles', async () => {
  const r = await reader();
  try {
    const source = material('source', {images: ['/api/image'], previous: [{url: '/api/materials/source/versions/1'}], attachments: [
      {url: '/api/video', kind: 'video', mime: 'video/webm'},
      {url: '/api/circle', kind: 'circle', mime: 'video/webm'},
    ]});
    await r.start([material('digest', {sources: [source], digest: {points: [{text: 'Тезис обзора', sources: [{id: 'source', url: source.url}]}]}})]);
    const base64 = await videoFixture(r.page);
    await r.page.evaluate(({base64, png}) => {
      readerTransport.replies.set('/api/image', {base64: png, mime: 'image/png'});
      readerTransport.replies.set('/api/video', {base64, mime: 'video/webm'});
      readerTransport.replies.set('/api/circle', {base64, mime: 'video/webm'});
      readerTransport.replies.set('/api/materials/source/versions/1', {body: {title: 'Предыдущий заголовок', text: 'Предыдущий текст', full:true, reason:'', images: ['/api/image'], attachments: []}});
    }, {base64, png});
    await r.page.getByRole('button', {name: /^Обзор/}).click();
    await expect(r.page.locator('#event-body')).toContainText('Тезис обзора');
    await r.page.locator('#event-sources summary').filter({hasText: 'Оригинал'}).click();
    await expect(r.page.locator('#source-media-source img')).toBeVisible();
    await expect(r.page.locator('#source-media-source video')).toHaveCount(2);
    await expect(r.page.locator('#source-media-source .circle')).toBeVisible();
    await r.page.locator('#event-sources summary').filter({hasText: 'Предыдущая версия'}).click();
    await expect(r.page.locator('#event-sources')).toContainText('Предыдущий текст');
    await expect(r.page.locator('#event-sources img')).toHaveCount(2);
    await r.page.evaluate(() => readerTransport.replies.set('POST /api/materials/digest/viewed', {body:{ok:true},next:[['/api/cards',{body:[]}]]}));
    await r.page.getByRole('button', {name: 'Просмотрено', exact: true}).click();
    await expect(r.page.locator('#event-sources')).toBeEmpty();
    assert.equal(await r.page.evaluate(() => browserResources.created.length), 4);
    assert.deepEqual(await r.page.evaluate(() => browserResources.revoked), await r.page.evaluate(() => browserResources.created));
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('title opens original in a new tab and resets for other cards', async () => {
  const r = await reader();
  try {
    await r.context.route('https://example.org/**', route => route.fulfill({body: '<h1>Original</h1>', contentType: 'text/html'}));
    await r.start([material('A'), material('B', {url: ''}), material('digest', {sources: [material('source')], digest: {points: []}})]);
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
    await expect(link).toHaveCount(0);
    await r.page.getByRole('button', {name: /^Обзор/}).click();
    await expect(r.page.locator('#event-panel')).toBeVisible();
    await expect(link).toHaveCount(0);
    assert.deepEqual(r.errors, []);
  } finally { await r.close(); }
});
