const {artifactPath} = require('./artifacts.cjs');
const {test} = require('node:test');
const {reader, expect} = require('./support.cjs');

test('add URL uses one form and automatically shows YouTube connection', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id: 'youtube', title: '3Blue1Brown', kind: 'youtube', transport: 'youtube', folder_id: 'f', original_urls: ['https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw'], importance: 'normal', initial_days: 1};
      t.replies.set('/api/subscriptions', {body: {folders: [{id: 'f', title: 'Учёба'}], feeds: [feed]}});
      t.replies.set('/api/feeds', {body: feed});
      t.replies.set('/api/feeds/verify', {body: {url: feed.original_urls[0], title: '3Blue1Brown', kind: 'youtube', verification: 'checked', existing: null}});
      t.replies.set('/api/settings/feed/youtube', {body: {...t.replies.get('/api/settings/oko/global').body, feed}});
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw');
    await expect(r.page.locator('#rss-add')).toBeDisabled();
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-title')).toHaveValue('3Blue1Brown');
    await r.page.locator('#rss-folder').selectOption('Учёба');
    await r.page.locator('#rss-add').click();
    await expect(r.page.locator('#feeds-dialog')).toBeVisible();
    await expect(r.page.locator('#rss-url')).toHaveValue('');
    await expect(r.page.locator('#feed-options')).toContainText('YouTube подключён');
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/feeds' && r.method === 'POST'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    await expect(r.page.locator('#rss-url')).toHaveValue('');
    await expect(r.page.locator('#rss-url')).toBeEnabled();
    await expect(r.page.locator('#rss-checked-fields')).toBeHidden();
    expect(request.body).toEqual({url: 'https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw', title: '3Blue1Brown', folder: 'Учёба', verification: 'checked', initial_history: {mode: 'count', count: 20, date: null, timezone: await r.page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)}});
  } finally { await r.close(); }
});

test('debug toggle shows server traceback and caught browser errors are sent while closed', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.api('debug', 'POST', {source:'collector', message:'broken source', traceback:'RuntimeError at discover', error_type:'RuntimeError', location:'discover:42'});
    await r.page.evaluate(() => readerTransport.replies.set('/api/collect', {status: 500, body: {detail: 'collection failed'}}));
    await expect(r.page.locator('#debug-panel')).toBeHidden();
    await r.page.locator('#collect').click();
    await r.page.waitForFunction(() => readerTransport.requests.some(r => r.url === '/api/debug' && r.method === 'POST' && r.body.message === 'collection failed'));
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-panel')).toBeVisible();
    await expect(r.page.locator('#debug-errors')).toContainText('broken source');
    await expect(r.page.locator('#debug-errors')).toContainText('collection failed');
    await r.page.evaluate(() => window.dispatchEvent(new ErrorEvent('error', {message: 'JS exploded', error: new Error('JS exploded')})));
    await expect(r.page.locator('#debug-errors')).toContainText('JS exploded');
    await r.page.locator('#status-close').click();
    await expect(r.page.locator('#debug-panel')).toBeHidden();
  } finally { await r.close(); }
});

test('changing URL rejects late verification, errors retry and duplicate opens existing settings', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [], feeds: []}});
      t.replies.set('/api/processors/llamacpp', {body: null});
      t.held.set('/api/feeds/verify', true);
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/old');
    await r.page.locator('#rss-check').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/feeds/verify'));
    await r.page.locator('#rss-url').fill('https://source.test/new');
    await r.page.evaluate(() => readerTransport.release('/api/feeds/verify', {url: 'https://source.test/old', title: 'Old', verification: 'old', existing: null}));
    await expect(r.page.locator('#rss-add')).toBeDisabled();
    await expect(r.page.locator('#rss-checked-fields')).toBeHidden();
    await r.page.evaluate(() => readerTransport.replies.set('/api/feeds/verify', {status: 400, body: {detail: 'Неверный RSS/Atom'}}));
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-message')).toHaveText('Неверный RSS/Atom');
    await r.page.evaluate(() => {
      const feed = {id: 'existing', title: 'Старое имя', kind: 'rss', transport: 'rss', importance: 'high', initial_days: 7, folder_id: 'f'};
      readerTransport.replies.set('/api/feeds/verify', {body: {existing: feed}});
      readerTransport.replies.set('/api/subscriptions', {body: {feeds: [feed], folders: [{id: 'f', title: 'Старая папка'}]}});
      readerTransport.replies.set('/api/settings/feed/existing', {body: {...readerTransport.replies.get('/api/settings/oko/global').body, feed}});
    });
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#feeds-dialog')).toBeVisible();
    await expect(r.page.locator('#owner-heading')).toHaveText('Фид: Старое имя');
    expect(await r.page.evaluate(() => readerTransport.requests.filter(r => r.url === '/api/feeds' && r.method === 'POST').length)).toBe(0);
  } finally { await r.close(); }
});

test('offline browser errors group, retain material scroll, and retry the same delivery', async () => {
  const {material} = require('./support.cjs');
  const r = await reader();
  try {
    await r.start([material('one', {text: 'Длинный текст. '.repeat(4000)})]);
    await r.page.locator('#cards button').click();
    await expect(r.page.locator('#title')).toHaveText('one');
    await r.page.evaluate(() => {
      document.querySelector('article').scrollTop = 500;
      readerTransport.replies.set('POST /api/debug', {status: 503, body: {detail: 'offline'}});
      for (let i = 0; i < 3; i++) window.dispatchEvent(new ErrorEvent('error', {error: new Error('offline-js'), message: 'offline-js'}));
    });
    const scroll = await r.page.locator('article').evaluate(node => node.scrollTop);
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-errors')).toContainText('offline-js');
    await expect(r.page.locator('#debug-errors')).toContainText('×3');
    await expect(r.page.locator('#debug-errors')).toContainText('не сохранено');
    await expect(r.page.locator('#title')).toHaveText('one');
    expect(await r.page.locator('article').evaluate(node => node.scrollTop)).toBe(scroll);
    const event = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/debug' && r.method === 'POST').body.event_id);
    await r.page.evaluate(() => readerTransport.replies.delete('POST /api/debug'));
    await expect(r.page.locator('#debug-errors')).not.toContainText('не сохранено', {timeout: 6000});
    await expect(r.page.locator('#debug-errors')).toContainText('×3');
    expect(await r.page.evaluate(id => readerTransport.requests.filter(r => r.url === '/api/debug' && r.method === 'POST' && r.body.event_id === id).length, event)).toBeGreaterThan(1);
    await r.page.locator('#status-close').click();
    expect(await r.page.locator('article').evaluate(node => node.scrollTop)).toBe(scroll);
  } finally { await r.close(); }
});

test('browser pending diagnostics mask quoted passwords and Basic credentials', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('POST /api/debug', {status: 503, body: {detail: 'offline'}});
      const message = 'password="correct horse battery staple" Authorization: Basic dXNlcjpwYXNz';
      window.dispatchEvent(new ErrorEvent('error', {error: new Error(message), message}));
    });
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-errors')).toContainText('[hidden]');
    await expect(r.page.locator('#debug-errors')).not.toContainText('horse');
    await expect(r.page.locator('#debug-errors')).not.toContainText('dXNlcjpwYXNz');
    const body = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/debug' && r.method === 'POST').body);
    expect(JSON.stringify(body)).not.toContain('horse');
    expect(JSON.stringify(body)).not.toContain('dXNlcjpwYXNz');
  } finally { await r.close(); }
});

test('switch works with keyboard and clearing history rejects a late journal response', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => window.dispatchEvent(new ErrorEvent('error', {error: new Error('old error')})));
    await r.page.locator('#status-toggle').focus();
    await r.page.keyboard.press('Space');
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-errors')).toContainText('old error');
    const size = await r.page.locator('#status-toggle').boundingBox();
    expect(size.width).toBeGreaterThan(size.height);
    await r.page.evaluate(() => {
      readerTransport.held.set('/api/debug', true);
      readerTransport.held.set('/api/status', true);
    });
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/debug'));
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/status'));
    const old = (await r.api('debug')).groups;
    // Release only the old GET; DELETE uses the normal transport immediately.
    await r.page.evaluate(() => readerTransport.held.delete('/api/debug'));
    await r.page.locator('#debug-clear').click();
    await expect(r.page.locator('#debug-empty')).toBeVisible();
    await expect(r.page.locator('#debug-count')).toHaveText('0');
    await r.page.evaluate(groups => readerTransport.release('/api/debug', {groups, persistence_error: ''}), old);
    await r.page.evaluate(() => readerTransport.release('/api/status', {stage: 'Готово', errors: [], sources: [], ready_sources:1, total_sources:1, debug_count: 31, debug_persistence_error: 'old disk error'}));
    await expect(r.page.locator('#debug-errors')).not.toContainText('old error');
    await expect(r.page.locator('#debug-count')).toHaveText('0');
    await expect(r.page.locator('#debug-status')).toHaveText('');
    await r.page.evaluate(() => window.dispatchEvent(new ErrorEvent('error', {error: new Error('new error')})));
    await expect(r.page.locator('#debug-errors')).toContainText('new error');
    await r.page.screenshot({path: artifactPath('debug-switches.png')});
  } finally { await r.close(); }
});

test('adding a feed selects existing folders or keeps a new folder draft across tabs and reopening', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [{id:'science',title:'Наука'}], feeds:[]}});
      t.replies.set('/api/feeds/verify', {body: {url:'https://source.test/feed',title:'Источник',kind:'rss',verification:'checked'}});
      t.replies.set('/api/feeds', {status:400,body:{detail:'Тестовый отказ'}});
      t.replies.set('/api/telegram/accounts', {body:['personal']});
      t.replies.set('/api/telegram/connect', {status:400,body:{detail:'Тестовый отказ'}});
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/feed');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-title')).toHaveValue('Источник');
    for (const prefix of ['rss', 'telegram']) {
      if (prefix === 'telegram') {
        await r.page.locator('#add-telegram-tab').click();
        await expect(r.page.locator('#telegram-account')).toHaveValue('personal');
        await r.page.locator('#telegram-peer').fill('@channel');
      }
      const endpoint = prefix === 'rss' ? '/api/feeds' : '/api/telegram/connect';
      await r.page.locator('#' + prefix + '-folder').selectOption('Наука');
      await expect(r.page.locator('#' + prefix + '-folder-new')).toBeHidden();
      await r.page.locator('#' + prefix + '-add').click();
      await expect(r.page.locator('#' + prefix + '-message')).toHaveText('Тестовый отказ');
      expect(await r.page.evaluate(url => readerTransport.requests.filter(request => request.url === url && request.method === 'POST').at(-1).body.folder, endpoint)).toBe('Наука');
      await r.page.locator('#' + prefix + '-folder').selectOption('');
      await expect(r.page.locator('#' + prefix + '-folder-new')).toBeVisible();
      await r.page.locator('#' + prefix + '-folder-new').fill('Новая папка ' + prefix);
      await r.page.locator(prefix === 'rss' ? '#add-telegram-tab' : '#add-rss-tab').click();
      await r.page.locator('#add-' + prefix + '-tab').click();
      await r.page.locator('#add-close').click();
      await r.page.locator('#add-open').click();
      await expect(r.page.locator('#' + prefix + '-folder')).toHaveValue('');
      await expect(r.page.locator('#' + prefix + '-folder-new')).toHaveValue('Новая папка ' + prefix);
      await r.page.locator('#' + prefix + '-add').click();
      await expect.poll(() => r.page.evaluate(url => readerTransport.requests.filter(request => request.url === url && request.method === 'POST').at(-1).body.folder, endpoint)).toBe('Новая папка ' + prefix);
      await expect(r.page.locator('#' + prefix + '-message')).toHaveText('Тестовый отказ');
    }
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('journal polling updates a group without losing its disclosure or keyboard focus', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.api('debug', 'POST', {source:'collector',message:'same group',error_type:'Error',location:'synthetic'});
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    const details = r.page.locator('#debug-errors details').filter({hasText:'same group'});
    const summary = details.locator('summary');
    await summary.click();
    await expect(details).toHaveAttribute('open','');
    await expect(summary).toBeFocused();
    await r.api('debug', 'POST', {source:'collector',message:'same group',error_type:'Error',location:'synthetic'});
    await expect(summary).toContainText('×2',{timeout:6000});
    await expect(details).toHaveAttribute('open','');
    await expect(summary).toBeFocused();
    const journal=artifactPath('react-journal-desktop.png');
    await r.page.screenshot({path:journal});console.info('Journal screenshot:',journal);
    await r.page.setViewportSize({width:390,height:844});
    const phone=artifactPath('react-journal-mobile.png');
    await r.page.screenshot({path:phone});console.info('Journal screenshot:',phone);
    await r.page.locator('#status-journal-back').click();
    await expect(r.page.locator('#status-journal-open')).toBeFocused();
  } finally {await r.close();}
});
