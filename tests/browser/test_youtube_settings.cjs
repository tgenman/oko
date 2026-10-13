const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {once} = require('node:events');
const {createInterface} = require('node:readline');
const {chromium, expect} = require('playwright/test');
const {reader} = require('./support.cjs');

test('real HTTP settings save proxy, preserve secrets and show the shared YouTube pause', {timeout:45000}, async t => {
  const root = path.resolve(__dirname, '../..');
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'oko-youtube-browser-'));
  const child = spawn(process.env.OKO_PYTHON || path.join(root, '.venv/bin/python'), ['-m', 'tests.support.youtube_browser_server', data], {cwd:root, stdio:['ignore', 'pipe', 'pipe']});
  let browser, stderr = '';
  child.stderr.on('data', chunk => {stderr += chunk;});
  const exit = once(child, 'exit'); exit.catch(() => {});
  const abort = () => {child.kill('SIGTERM'); void browser?.close().catch(() => {});};
  t.signal.addEventListener('abort', abort, {once:true});
  try {
    const lines = createInterface({input:child.stdout});
    let timer;
    const deadline = new Promise((_, reject) => {timer = setTimeout(() => reject(new Error('HTTP fixture timeout')), 10000);});
    let first;
    try {first = await Promise.race([once(lines, 'line'), exit.then(() => {throw new Error(stderr);}), deadline]);}
    finally {clearTimeout(timer);}
    const {origin, token} = JSON.parse(first[0]); lines.close();
    const headers = {Authorization:'Bearer ' + token, 'Content-Type':'application/json'};
    async function api(url, method='GET', body) {
      const response = await fetch(origin + '/api/' + url, {method, headers, body:body === undefined ? undefined : JSON.stringify(body)});
      assert.equal(response.status, 200, await response.clone().text()); return response.json();
    }
    await expect.poll(async () => {try {return (await fetch(origin)).status;} catch {return 0;}}, {timeout:10000}).toBe(200);
    await api('settings/oko/global', 'PATCH', {theses:false, events:false, digests:false});
    browser = await chromium.launch({headless:true, executablePath:process.env.OKO_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    const page = await browser.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/#token=' + token);
    await page.locator('#settings-open').click();
    await expect(page.locator('#youtube-connection')).toHaveText('Прямое подключение');
    await page.locator('#youtube-mode').selectOption('proxy');
    await page.locator('#youtube-address').fill('socks5://proxy.test:8000');
    await page.locator('#youtube-save').click();
    await expect(page.locator('#youtube-message')).toContainText('Некорректные настройки');
    await page.locator('#youtube-address').fill('http://proxy.test:8000');
    await page.locator('#youtube-username').fill('fixture-user');
    await page.locator('#youtube-password').fill('fixture-password');
    await page.locator('#settings-close').click();
    await page.locator('#settings-open').click();
    await expect(page.locator('#youtube-password')).toHaveValue('fixture-password');
    await page.locator('#youtube-save').click();
    await expect(page.locator('#youtube-message')).toHaveText('Настройки сохранены');
    await expect(page.locator('#youtube-password')).toHaveValue('');
    await expect(page.locator('#youtube-password')).toHaveAttribute('placeholder', 'Пароль сохранён');
    await page.locator('#youtube-address').fill('http://proxy.test:8001');
    await page.locator('#youtube-password').fill('temporary-edit');
    await page.locator('#youtube-password').fill('');
    await page.locator('#youtube-save').click();
    await expect.poll(async () => (await api('youtube')).address).toBe('http://proxy.test:8001');
    assert.equal((await api('youtube')).has_password, true);
    assert.ok(!JSON.stringify(await api('youtube')).includes('fixture-'));
    await page.locator('#youtube-clear').check();
    await page.locator('#youtube-save').click();
    await expect.poll(async () => (await api('youtube')).has_password).toBe(false);
    await page.locator('#youtube-mode').selectOption('direct');
    await page.locator('#youtube-save').click();
    await expect.poll(async () => (await api('youtube')).mode).toBe('direct');
    const plan = await api('import', 'POST', {opml:'<opml><body><outline text="YouTube" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa"/></body></opml>'});
    await api('feeds/' + plan.feeds[0].id, 'PATCH', {importance:'high'});
    await expect.poll(async () => (await api('youtube')).paused).toBe(true);
    await expect(page.locator('#youtube-pause')).toContainText('Получение субтитров приостановлено');
    await expect(page.locator('#youtube-pause')).toContainText('Следующая попытка не раньше');
    assert.ok(!JSON.stringify(await api('debug')).includes('fixture-password'));
    assert.deepEqual(errors, []);
  } finally {
    t.signal.removeEventListener('abort', abort);
    try {if (browser) await browser.close();} finally {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill('SIGTERM'); const timer = setTimeout(() => child.kill('SIGKILL'), 3000);
        try {await exit;} finally {clearTimeout(timer);}
      }
      await fs.rm(data, {recursive:true, force:true});
    }
  }
});

test('late settings replies do not overwrite a newer draft or reopen a closed form', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/subscriptions', {body:{folders:[], feeds:[]}});
      readerTransport.replies.set('/api/processors/llamacpp', {body:null});
    });
    await r.page.locator('#settings-open').click();
    await expect(r.page.locator('#youtube-connection')).toHaveText('Прямое подключение');
    await r.page.locator('#youtube-mode').selectOption('proxy');
    await r.page.locator('#youtube-address').fill('http://first.test:8000');
    await r.page.locator('#youtube-password').fill('first-password');
    await r.page.evaluate(() => readerTransport.held.set('/api/youtube', true));
    await r.page.locator('#youtube-save').click();
    await expect.poll(() => r.page.evaluate(() => readerTransport.pending.has('/api/youtube'))).toBe(true);
    await r.page.locator('#youtube-address').fill('http://second.test:8000');
    await r.page.locator('#youtube-password').fill('second-password');
    await r.page.locator('#settings-close').click();
    await r.page.evaluate(() => readerTransport.release('/api/youtube', {mode:'proxy', address:'http://first.test:8000', has_password:true, has_username:false, paused:false, next_at:0, strikes:0, reason:''}));
    await expect(r.page.locator('#settings-dialog')).not.toBeVisible();
    await r.page.locator('#settings-open').click();
    await expect(r.page.locator('#youtube-address')).toHaveValue('http://second.test:8000');
    await expect(r.page.locator('#youtube-password')).toHaveValue('second-password');
    await expect(r.page.locator('#youtube-message')).toHaveText('');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});
