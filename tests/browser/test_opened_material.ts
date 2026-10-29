import type {Page} from 'playwright';
import {test} from 'node:test';
import {artifactPath} from './artifacts';
import {reader, material, expect} from './support.ts';
import assert from 'node:assert/strict';


test('opened publication shows variant B with avatar and wrapping long feed and publication titles', async () => {
 const r=await reader();try {
  const avatarUrl='/api/v3/feeds/feed/avatar/'+'a'.repeat(64);
  const feedTitle='Луи · заметки о детстве, книгах и повседневной жизни — длинное название канала для чтения';
  const publicationTitle='Анализирую всё подряд, особенно детство. '+ 'ОченьДлинныйЗаголовокБезПробелов'.repeat(2);
  await r.start([material('one',{title:publicationTitle}),material('two',{feed_id:'second'})]);
  await r.page.evaluate(({avatarUrl,feedTitle})=>{const t=readerTransport;
   t.replies.set('/api/v3/reading-composition',{body:{count:2,folders:[{id:'f',title:'Каналы',position:0,count:2}],feeds:[{count:1,has_unviewed:true,feed:{id:'feed',title:feedTitle,folder_id:'f',identity:'@fixture',kind:'telegram',connection:'needs_connection',access:{kind:'unconfigured',source_locator:'@fixture'},importance:'normal',original_urls:['https://t.me/fixture'],initial_days:1,public_ai:false,history:{state:'absent'},avatar_url:avatarUrl}},{count:1,has_unviewed:true,feed:{id:'second',title:'Другой фид',folder_id:'f',kind:'rss',connection:'ready',identity:'https://second.test/feed',transport:'rss',importance:'normal',original_urls:['https://second.test/feed'],initial_days:1,public_ai:false,history:{state:'absent'}}}]}});
   t.replies.set(avatarUrl,{mime:'image/svg+xml',base64:'PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI2NCIgaGVpZ2h0PSI2NCI+PHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjYjZiOGQxIi8+PGNpcmNsZSBjeD0iNTAiIGN5PSIxMCIgcj0iMjUiIGZpbGw9IiNlOGQ0YjciLz48cGF0aCBkPSJNMCA1NVEzMCAxNSA2NCA1NVY2NEgwIiBmaWxsPSIjNmU3YTc4Ii8+PHRleHQgeD0iMzIiIHk9IjQyIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmaWxsPSJ3aGl0ZSIgZm9udC1mYW1pbHk9InNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMjgiPtCbPC90ZXh0Pjwvc3ZnPg=='});
  },{avatarUrl,feedTitle});
  await expect(r.page.locator('[data-feed="feed"]')).toHaveAttribute('title',feedTitle);
  await r.page.locator('#cards button').filter({hasText:publicationTitle}).click();
  await expect(r.page.locator('#material-feed-title')).toHaveText(feedTitle);
  const avatar=r.page.locator('#material-byline img');
  await expect(avatar).toHaveJSProperty('naturalWidth',64);
  expect((await avatar.boundingBox())!.width).toBe(44);
  await expect(r.page.locator('#date')).not.toBeEmpty();
  const layout=await r.page.evaluate(()=>{const title=document.getElementById('title')!,byline=document.getElementById('material-byline')!,text=document.getElementById('text')!;return {titleBottom:title.getBoundingClientRect().bottom,bylineTop:byline.getBoundingClientRect().top,bylineBottom:byline.getBoundingClientRect().bottom,textTop:text.getBoundingClientRect().top,overflow:title.scrollWidth>title.clientWidth||byline.scrollWidth>byline.clientWidth};});
  expect(layout.bylineTop).toBeGreaterThanOrEqual(layout.titleBottom);
  expect(layout.textTop).toBeGreaterThan(layout.bylineBottom);
  expect(layout.overflow).toBe(false);
  await r.page.screenshot({path:artifactPath('opened-feed-identity-b.png')});
  await r.page.locator('#cards button').filter({hasText:'two'}).click();
  await expect(r.page.locator('#material-feed-title')).toHaveText('Другой фид');
  await expect(avatar).toHaveCount(0);
  await expect(r.page.locator('#material-byline .source-icon')).toBeVisible();
  await r.page.locator('#cards button').filter({hasText:publicationTitle}).click();
  await expect(avatar).toHaveJSProperty('naturalWidth',64);
  await r.page.evaluate(()=>{const nav=readerTransport.replies.get('/api/v3/reading-composition')!.body!;const entry=nav.feeds[0];if('feed' in entry&&entry.feed&&typeof entry.feed==='object'&&'avatar_url' in entry.feed)delete entry.feed.avatar_url;});
  await expect(avatar).toHaveCount(0);
  await expect(r.page.locator('#material-byline .source-icon')).toBeVisible();
  await r.page.evaluate(()=>{const t=readerTransport,url='/api/v3/feeds/feed/avatar/'+'b'.repeat(64);t.replies.set(url,{mime:'image/png',base64:'bm90IGFuIGltYWdl'});const entry=t.replies.get('/api/v3/reading-composition')!.body!.feeds[0];if('feed' in entry&&entry.feed&&typeof entry.feed==='object')Object.assign(entry.feed,{avatar_url:url,title:'Канал с повреждённым аватаром'});});
  await expect(r.page.locator('#material-feed-title')).toHaveText('Канал с повреждённым аватаром');
  await expect(avatar).toHaveCount(0);
  await expect(r.page.locator('#material-byline .source-icon')).toBeVisible();
  await r.page.evaluate(avatarUrl=>{const nav=readerTransport.replies.get('/api/v3/reading-composition')!.body!;const entry=nav.feeds[0];if('feed' in entry&&entry.feed&&typeof entry.feed==='object')Object.assign(entry.feed,{avatar_url:avatarUrl,title:'Переименованный канал'});},avatarUrl);
  await expect(avatar).toHaveJSProperty('naturalWidth',64);
  await expect(r.page.locator('#material-feed-title')).toHaveText('Переименованный канал');
  const retainedAvatar=await avatar.getAttribute('src');
  await r.page.evaluate(()=>{const nav=readerTransport.replies.get('/api/v3/reading-composition')!.body!;nav.feeds=nav.feeds.filter(entry=>'feed' in entry&&entry.feed&&typeof entry.feed==='object'&&'id' in entry.feed&&entry.feed.id!=='feed');});
  await expect(r.page.locator('[data-feed="feed"]')).toHaveCount(0);
  await expect(r.page.locator('#material-feed-title')).toHaveText('Переименованный канал');
  await expect(avatar).toHaveAttribute('src',retainedAvatar!);
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('switch clears old material immediately and ignores late opening errors', async () => {
  const r = await reader();
  try {
    await r.start([material('A'), material('B'), material('C')]);
    await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.evaluate(() => { readerTransport.held.set('/fixture/material/B', true); readerTransport.held.set('/fixture/material/C', true); });
    await r.page.getByRole('button', {name: /^B/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/fixture/material/B'));
    await expect(r.page.locator('#text')).toHaveText('');
    await expect(r.page.locator('#empty')).toHaveText('Loading item…');
    await r.page.locator('#cards').getByRole('button', {name: /^C/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/fixture/material/C'));
    await r.page.evaluate(body => readerTransport.release('/fixture/material/C', body), material('C'));
    await expect(r.page.locator('#text')).toHaveText('Текст C');
    await r.page.evaluate(() => readerTransport.release('/fixture/material/B', {detail: 'Поздняя ошибка B'}, 500));
    await settle(r.page);
    await expect(r.page.locator('#text')).toHaveText('Текст C');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('late success of an old opening cannot replace the selected card', async () => {
  const r = await reader();
  try {
    await r.start([material('A'), material('B')]);
    await r.page.evaluate(() => readerTransport.held.set('/fixture/material/A', true));
    await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/fixture/material/A'));
    await r.page.getByRole('button', {name: /^B/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    await r.page.evaluate(body => readerTransport.release('/fixture/material/A', body), material('A'));
    await settle(r.page);
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

async function settle(page: Page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}


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

test('viewing retains loaded media and playback, rejects late media and switching releases URLs', async () => {
  const r = await reader();
  try {
    await r.start([material('A', {images: ['/api/v3/materials/A/images/image-2.png'], attachments: [
      {url: '/api/v3/materials/A/media/media-1.webm', kind: 'video', mime: 'video/webm'},
      {url: '/api/v3/materials/A/media/media-2.webm', kind: 'circle', mime: 'video/webm'},
    ]}),material('B')]);
    const base64 = await videoFixture(r.page);
    await r.page.evaluate(({base64, png}) => {
      readerTransport.replies.set('/api/v3/materials/A/images/image-2.png', {base64: png, mime: 'image/png'});
      readerTransport.replies.set('/api/v3/materials/A/media/media-1.webm', {base64, mime: 'video/webm'});
      readerTransport.held.set('/api/v3/materials/A/media/media-2.webm', true);
    }, {base64, png});
    await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
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
    await r.page.evaluate(() => readerTransport.replies.set('POST /api/v3/materials/A/viewed', {body:{ok:true},next:[['/api/v3/cards',{body:[{...readerTransport.replies.get('/fixture/material/B')!.body!}]}]]}));
    await r.page.getByRole('button', {name: 'Mark as Read', exact: true}).click();
    await expect(r.page.locator('#material-retained')).toBeVisible();
    assert.equal(await r.page.evaluate(() => !testVideo.paused && testVideo.hasAttribute('src')), true);
    assert.equal(await r.page.evaluate(() => browserResources.created.length), 2);
    assert.deepEqual(await r.page.evaluate(() => browserResources.revoked), []);
    await r.page.evaluate(() => readerTransport.release('/api/v3/materials/A/media/media-2.webm', 'late circle'));
    await settle(r.page);
    await expect(r.page.locator('#images video')).toHaveCount(1);
    assert.equal(await r.page.evaluate(() => browserResources.created.length), 2);
    await r.page.locator('[data-card="B"]').click();
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    assert.equal(await r.page.evaluate(() => testVideo.paused && !testVideo.hasAttribute('src')), true);
    assert.deepEqual(await r.page.evaluate(() => browserResources.revoked), await r.page.evaluate(() => browserResources.created));
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('viewing a publication in another tab closes its pending opening but preserves unrelated folder', async () => {
  const r = await reader();
  try {
    const source = material('source');
    const unrelated = material('other', {folder_id: 'other-folder',feed_id:'other-feed'});
    await r.start([source, unrelated]);
    const otherTab = await r.context.newPage(); await r.start([source], otherTab);
    await r.page.evaluate(() => readerTransport.held.set('/fixture/material/source', true));
    await r.page.getByRole('button', {name: /^source/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/fixture/material/source'));
    await otherTab.getByRole('button', {name: /^source/}).click();
    await expect(otherTab.locator('#text')).toHaveText('Текст source');
    await otherTab.evaluate(() => readerTransport.replies.set('POST /api/v3/materials/source/viewed', {body:{ok:true},next:[['/api/v3/cards',{body:[]}]]}));
    await otherTab.getByRole('button', {name: 'Mark as Read', exact: true}).click();
    await expect(r.page.locator('#empty')).toHaveText('Select an item');
    await r.page.evaluate(body => readerTransport.release('/fixture/material/source', body), source);
    await settle(r.page);
    await expect(r.page.locator('#empty')).toHaveText('Select an item');
    await r.page.locator('[data-card="other"]').click();
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
      await r.page.evaluate(() => readerTransport.held.set('/fixture/material/A', true));
      await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
      await r.page.waitForFunction(() => readerTransport.pending.has('/fixture/material/A'));
      if (interruption === 'stop') await r.page.evaluate(()=>{readerTransport.replies.get('/api/v3/status')!.body!.stopped=true;});
      else {
        await r.page.evaluate(() => readerTransport.replies.set('/api/v3/status', {status: 503, body: {detail: 'Связь потеряна'}}));
      }
      await expect(r.page.locator('#empty')).toHaveText('Select an item');
      await r.page.evaluate(body => readerTransport.release('/fixture/material/A', body), material('A'));
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
    await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
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
  await r.page.evaluate(()=>{const t=readerTransport;t.held.set('/fixture/material/long',true);t.replies.get('/api/v3/cards')!.body![0].ai={status:'pending',error:'Запрещена публичная AI-обработка'};});
  await r.page.waitForFunction(()=>readerTransport.pending.has('/fixture/material/long'));
  await expect(r.page.locator('#text')).toContainText('Длинный текст');
  expect(await r.page.locator('article').evaluate(node=>node.scrollTop)).toBe(before);
  await r.page.evaluate(()=>readerTransport.release('/fixture/material/long',readerTransport.replies.get('/fixture/material/long')!.body));
  await expect(r.page.locator('#text')).toContainText('Длинный текст');
  expect(await r.page.locator('article').evaluate(node=>node.scrollTop)).toBe(before);
 }finally{await r.close();}
});

test('new text waits for a button, keeps its paragraph and never mixes summaries across revisions',async()=>{
  const r=await reader();try{
    const text=Array.from({length:90},(_,i)=>'Абзац '+i+' '+('сохранённый текст '.repeat(10))).join('\n\n');
    const old=material('A',{text,version:'old',theses:{points:[{text:'Старые тезисы',sources:[]}]}});
    await r.start([old]);await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#theses')).toHaveText('Старые тезисы');
    await r.page.locator('#text [data-reading-paragraph]').nth(25).evaluate(node=>node.scrollIntoView({block:'start'}));
    const before=await r.page.locator('#text [data-reading-paragraph]').nth(25).boundingBox();
    const fresh={...old,text:'Новый первый абзац\n\n'.repeat(8)+text,version:'new',theses:{points:[{text:'Новые тезисы',sources:[]}]}};
    await r.page.evaluate(fresh=>readerTransport.replies.set('/fixture/material/A',{body:fresh}),fresh);
    await expect(r.page.locator('#material-update-button')).toBeVisible();
    await expect(r.page.locator('#text')).toHaveText(text);
    await expect(r.page.locator('#theses')).toHaveText('Старые тезисы');
    expect(Math.abs((await r.page.locator('#text [data-reading-paragraph]').nth(25).boundingBox())!.y-before!.y)).toBeLessThan(2);
    // Click without scrolling the article to the update banner.
    await r.page.screenshot({path:artifactPath('version-available.png')});
    await r.page.locator('#material-update-button').click();
    await expect(r.page.locator('#text')).toHaveText(fresh.text);
    await expect(r.page.locator('#theses')).toHaveText('Новые тезисы');
    expect(Math.abs((await r.page.locator('#text [data-reading-paragraph]').nth(33).boundingBox())!.y-before!.y)).toBeLessThan(2);
    await r.page.screenshot({path:artifactPath('version-switched.png')});
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});

test('summary and media append independently without replacing a playing video',async()=>{
  const r=await reader();try{
    const old=material('A',{text:'Текст A\n'.repeat(100),attachments:[{url:'/api/v3/materials/A/media/one.webm',kind:'video',mime:'video/webm'}]});
    await r.start([old]);const base64=await videoFixture(r.page);
    await r.page.evaluate(base64=>readerTransport.replies.set('/api/v3/materials/A/media/one.webm',{base64,mime:'video/webm'}),base64);
    await r.page.locator('[data-card="A"]').click();
    await r.page.locator('#images video').evaluate(async node=>{const video=node as HTMLVideoElement;window.testVideo=video;video.muted=true;video.loop=true;await video.play();});
    await r.page.locator('article').evaluate(node=>node.scrollTop=500);
    const top=await r.page.locator('article').evaluate(node=>node.scrollTop);
    const next={...old,theses:{points:[{text:'Готовые тезисы',sources:[]}]},images:['/api/v3/materials/A/images/new.png']};
    await r.page.evaluate(({next,png})=>{readerTransport.replies.set('/api/v3/materials/A/images/new.png',{base64:png,mime:'image/png'});readerTransport.replies.set('/fixture/material/A',{body:next});},{next,png});
    await expect(r.page.locator('#theses')).toHaveText('Готовые тезисы');
    await expect(r.page.locator('#images img')).toHaveCount(1);
    assert.equal(await r.page.evaluate(()=>document.querySelector('#images video')===testVideo&&!testVideo.paused),true);
    // The new summary above the reading position must not move its paragraph.
    expect(await r.page.locator('article').evaluate(node=>node.scrollTop)).toBeGreaterThan(top);
    assert.equal(await r.page.evaluate(()=>browserResources.created.length),2);
    assert.deepEqual(await r.page.evaluate(()=>browserResources.revoked),[]);
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});

test('removed cards and connection loss keep loaded text, reload drops it',async()=>{
  const r=await reader();try{
    await r.start([material('A')]);await r.page.locator('[data-card="A"]').click();await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/cards',{body:[]}));
    await expect(r.page.locator('#cards button')).toHaveCount(0);await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/status',{status:503,body:{detail:'Offline'}}));
    await expect(r.page.locator('#collection-progress')).toContainText('Нет связи');await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.reload();await expect(r.page.locator('#material')).toBeHidden();
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});

for(const fallback of ['neighbour','relative','filtered'])test('version replacement preserves '+fallback+' reading position',async()=>{
  const r=await reader();try{
    const lines=Array.from({length:90},(_,i)=>'Абзац '+i+' '+('длинный текст '.repeat(15)));
    const filter=fallback==='filtered'?{status:'done' as const,hidden:false,reason:'Фильтр',removed:['Реклама']}:undefined;
    const old=material('A',{text:lines.join('\n\n')+'\nРеклама',version:'old',...(filter?{filter}:{})});
    await r.start([old]);await r.page.locator('[data-card="A"]').click();
    const selector=fallback==='filtered'?'#filtered-text':'#text';
    await expect(r.page.locator(selector)).toContainText('Абзац 25');
    await r.page.locator(selector+' [data-reading-paragraph]').nth(25).evaluate(node=>node.scrollIntoView({block:'start'}));
    const anchor=await r.page.locator(selector+' [data-reading-paragraph]').nth(fallback==='neighbour'?24:25).boundingBox();
    const ratio=await r.page.locator('article').evaluate(node=>node.scrollTop/(node.scrollHeight-node.clientHeight));
    const nextLines=fallback==='relative'?lines.map(line=>line.replace('Абзац','Заменён')):lines.filter((_,i)=>fallback!=='neighbour'||i!==25);
    const fresh={...old,text:'Вставка\n\n'.repeat(8)+nextLines.join('\n\n')+(fallback==='relative'?'\nНовый конец':'\nРеклама'),version:'new'};
    await r.page.evaluate(fresh=>readerTransport.replies.set('/fixture/material/A',{body:fresh}),fresh);
    await expect(r.page.locator('#material-update-button')).toBeVisible();await r.page.locator('#material-update-button').click();
    await expect(r.page.locator(selector)).toContainText(fallback==='relative'?'Заменён':'Вставка');
    if(fallback==='relative')expect(Math.abs(await r.page.locator('article').evaluate(node=>node.scrollTop/(node.scrollHeight-node.clientHeight))-ratio)).toBeLessThan(.01);
    else expect(Math.abs((await r.page.locator(selector+' [data-reading-paragraph]').nth(fallback==='neighbour'?32:33).boundingBox())!.y-anchor!.y)).toBeLessThan(2);
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});

test('automatic fallback after removing the selected feed keeps the loaded material',async()=>{
  const r=await reader();try{
    await r.start([material('A')]);await r.page.locator('[data-feed="feed"]').click();await r.page.locator('[data-card="A"]').click();await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.evaluate(()=>{readerTransport.replies.set('/api/v3/cards',{body:[]});readerTransport.replies.set('/api/v3/reading-composition',{body:{count:0,folders:[],feeds:[]}});});
    await expect(r.page.locator('[data-feed="feed"]')).toHaveCount(0);await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.locator('#all-folders').click();await expect(r.page.locator('#material')).toBeHidden();
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});

test('storage replacement rejects old media replies and stages the replacement before loading its assets',async()=>{
  const r=await reader();try{
    const url='/api/v3/materials/A/images/one.png';
    await r.page.addInitScript(()=>{
      const fetch=window.fetch.bind(window);
      window.fetch=async(url,options)=>{
        if(!['/api/v3/status','/api/v3/navigation'].includes(String(url))||!readerTransport.held.has('test-storage-reset'))return fetch(url,options);
        const headers=new Headers(options?.headers);headers.delete('If-None-Match');
        const reply=await fetch(url,{...options,headers});
        const responseHeaders=new Headers(reply.headers);responseHeaders.set('X-Oko-Storage-Id','2d879353-68a3-4919-831c-32cb82c3d55f');
        return new Response(await reply.text(),{status:reply.status,headers:responseHeaders});
      };
    });
    await r.start([material('A',{version:'old',images:[]})]);
    await r.page.evaluate(url=>{const reply=readerTransport.replies.get('/fixture/material/A');if(!reply?.body||typeof reply.body!=='object')throw new Error('Missing material fixture');reply.body={...reply.body,images:[url]};},url);
    await r.page.evaluate(url=>readerTransport.held.set(url,true),url);
    await r.page.locator('[data-card="A"]').click();await expect(r.page.locator('#text')).toHaveText('Текст A');await r.page.waitForFunction(url=>readerTransport.pending.has(url),url);
    await r.page.evaluate(()=>{
      readerTransport.held.set('test-storage-reset',true);
      readerTransport.replies.set('/fixture/material/A',{body:{...readerTransport.replies.get('/fixture/material/A')!.body!,text:'Другой экземпляр',version:'old'}});
    });
    // Wait for the request after reset: the already in-flight pre-reset poll
    // can discover the new text before the injected reset is accepted.
    await r.page.waitForFunction(()=>readerTransport.requests.filter(request=>request.url==='/api/v3/publication/A').length>=2);
    await expect(r.page.locator('#material-update-button')).toBeVisible();
    await r.page.evaluate(({url,png})=>{readerTransport.release(url,'late old asset');readerTransport.replies.set(url,{base64:png,mime:'image/png'});},{url,png});
    await settle(r.page);await expect(r.page.locator('#text')).toHaveText('Текст A');await expect(r.page.locator('#images img')).toHaveCount(0);assert.deepEqual(await r.page.evaluate(()=>browserResources.created),[]);
    await r.page.locator('#material-update-button').click();await expect(r.page.locator('#text')).toHaveText('Другой экземпляр');await expect(r.page.locator('#images img')).toHaveCount(1);assert.equal(await r.page.evaluate(()=>browserResources.created.length),1);
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});

test('reconnect resumes missing media for unchanged text without replacing loaded content',async()=>{
  const r=await reader();try{
    const url='/api/v3/materials/A/images/one.png';await r.start([material('A',{images:[url]})]);
    await r.page.evaluate(url=>readerTransport.held.set(url,true),url);await r.page.locator('[data-card="A"]').click();await expect(r.page.locator('#text')).toHaveText('Текст A');await r.page.waitForFunction(url=>readerTransport.pending.has(url),url);
    await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/status',{status:503,body:{detail:'Offline'}}));await expect(r.page.locator('#collection-progress')).toContainText('Нет связи');
    await r.page.evaluate(({url,png})=>{readerTransport.release(url,'stale bytes');readerTransport.replies.set(url,{base64:png,mime:'image/png'});readerTransport.replies.set('/api/v3/status',{body:{stopped:false,ready_sources:1,total_sources:1}});},{url,png});
    await expect(r.page.locator('#collection-progress')).not.toContainText('Нет связи');await expect(r.page.locator('#text')).toHaveText('Текст A');await expect(r.page.locator('#images img')).toHaveCount(1);assert.equal(await r.page.evaluate(()=>browserResources.created.length),1);
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});

test('current publication text is first-class and previous versions start collapsed',async()=>{
 const r=await reader();try{
  await r.start([material('A',{originals:false,title:'AI-агент Tolan, которым пользуются в основном женщины, поднял $30 млн',text:'Сегодня новостью про AI-агента сложно удивить. Но я сегодня погрузился [в историю от Upstarts Media про стартап Tolan](https://www.upstartsmedia.com/p/tolan-raises-30m-for-ai-companion-you-trust).\n\nОснователи Tolan — Квинтен Фармер, Аджай Мехта и Эван Голдшмидт. Их агент помогает с повседневными задачами.\n\nОн может запоминать, о чём с ним разговаривали, и поддерживать беседу на базе этих знаний.'}),material('B')]);
  await r.page.locator('[data-card="A"]').click();
  await expect(r.page.locator('#text')).toBeVisible();
  assert.equal(await r.page.locator('#text').locator('xpath=ancestor::details').count(),0);
  await expect(r.page.locator('#original-panel')).toHaveCount(0);
  await expect(r.page.locator('#versions')).toHaveCount(0);
  await r.page.screenshot({path:artifactPath('latest-publication-desktop.png')});
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});

test('reply context is a compact separate card with safe navigation and local expansion', async () => {
  const r=await reader();
  const excerpt='Цитируемый фрагмент другого автора. '.repeat(20)+'Последние слова фрагмента.';
  try {
    await r.start([material('A',{reply_preview:{author:'Другой автор',text:excerpt,source_url:'https://t.me/fixture/10'}}),material('B')]);
    await r.page.locator('#cards').getByRole('button',{name:/^A/}).click();
    const card=r.page.locator('#material .reply-preview');
    await expect(card).toBeVisible();
    await expect(r.page.locator('#text')).toHaveText('Текст A');
    const link=card.locator('a');
    assert.equal(await link.getAttribute('href'),'https://t.me/fixture/10');
    assert.equal(await link.getAttribute('target'),'_blank');
    assert.equal(await link.getAttribute('rel'),'noopener noreferrer');
    assert.equal(await card.locator('.reply-preview-text').evaluate(e=>getComputedStyle(e).webkitLineClamp),'2');
    await r.page.screenshot({path:artifactPath('telegram-reply-compact-desktop.png')});
    // Local expansion must not request content; regular version polling is
    // independent of the card, as is renewing the browser presence lease.
    const heartbeat=await r.api('client-sessions','POST');
    const requests=await r.page.evaluate(()=>readerTransport.requests);
    const foreground=(q:(typeof requests)[number])=>!(q.method==='PUT'&&/^\/api\/v3\/client-sessions\/[^/]+$/.test(q.url)||q.method==='GET'&&(q.url.startsWith('/api/v3/cards?')||q.url.startsWith('/api/v3/publication/')||['/api/v3/status','/api/v3/navigation','/api/v3/collection-status'].includes(q.url)));
    const start=await r.page.evaluate(()=>readerTransport.requests.length);
    assert.ok(requests.some(q=>q.url==='/api/v3/publication/A'&&q.method==='GET'));
    await card.getByRole('button',{name:'Весь фрагмент'}).focus();
    await r.page.keyboard.press('Enter');
    await expect(card.getByRole('button',{name:'Свернуть'})).toHaveAttribute('aria-expanded','true');
    assert.equal(await card.locator('.reply-preview-text').textContent(),excerpt);

    await r.page.evaluate(async({id,token})=>{const reply=await fetch('/api/v3/client-sessions/'+id,{method:'PUT',headers:{Authorization:'Bearer '+token}});if(!reply.ok)throw new Error('Heartbeat failed: '+reply.status);},{id:heartbeat.id,token:r.token});
    await r.page.evaluate(async token=>{
      for(const path of ['/api/v3/status','/api/v3/navigation']){
        const reply=await fetch(path,{headers:{Authorization:'Bearer '+token}});
        if(!reply.ok)throw new Error('Background status failed: '+path);
      }
    },r.token);
    const observed=await r.page.evaluate(()=>readerTransport.requests);
    assert.ok(observed.some(q=>q.method==='PUT'&&q.url==='/api/v3/client-sessions/'+heartbeat.id));
    assert.deepEqual(observed.filter(foreground),requests.filter(foreground));
    assert.ok(observed.slice(start).filter(q=>q.url.startsWith('/api/v3/publication/')).every(q=>q.method==='GET'&&q.url==='/api/v3/publication/A'));
    const polls=await r.page.evaluate(start=>readerTransport.requests.slice(start).filter(q=>['/api/v3/status','/api/v3/navigation'].includes(q.url)),start);
    assert.ok(polls.length>=2);
    assert.ok(polls.every(q=>q.method==='GET'&&!q.body));
    assert.ok(!observed.some(q=>q.url==='/api/v3/reading/check'));
    await r.page.screenshot({path:artifactPath('telegram-reply-desktop.png')});
    await r.page.setViewportSize({width:1120,height:900});
    await card.getByRole('button',{name:'Свернуть'}).click();
    await r.page.screenshot({path:artifactPath('telegram-reply-narrow.png')});
    assert.equal(await card.evaluate(e=>e.scrollWidth<=e.clientWidth),true);
    await r.page.setViewportSize({width:1280,height:900});
    await r.page.locator('#cards').getByRole('button',{name:/^B/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст B');
    await expect(card).toHaveCount(0);
    await r.page.locator('#cards').getByRole('button',{name:/^A/}).click();
    await expect(card.getByRole('button',{name:'Весь фрагмент'})).toHaveAttribute('aria-expanded','false');
    assert.deepEqual(r.errors,[]);
  } finally {await r.close();}
});

test('reply context stays with its revision and historical previews use their own source',async()=>{
  const r=await reader();try{
    const old=material('A',{version:'old',reply_preview:{author:null,text:'Старый фрагмент '.repeat(18),source_url:null}});
    await r.start([old]);await r.page.locator('[data-card="A"]').click();
    const card=r.page.locator('#reply-preview');
    await expect(card).toBeVisible();
    await expect(card.locator('strong')).toHaveText('Ответ на сообщение');
    await expect(card.locator('a')).toHaveCount(0);
    await card.getByRole('button',{name:'Весь фрагмент'}).click();
    const summarized={...old,theses:{points:[{text:'Новые тезисы той же версии',sources:[]}]}};
    await r.page.evaluate(value=>readerTransport.replies.set('/fixture/material/A',{body:value}),summarized);
    await expect(r.page.locator('#theses')).toHaveText('Новые тезисы той же версии');
    await expect(card.getByRole('button',{name:'Свернуть'})).toHaveAttribute('aria-expanded','true');
    const fresh={...summarized,version:'new',reply_preview:{author:'Другой автор',text:'Новый контекст',source_url:'https://t.me/fixture/20'}};
    await r.page.evaluate(value=>readerTransport.replies.set('/fixture/material/A',{body:value}),fresh);
    await expect(r.page.locator('#material-update-button')).toBeVisible();
    await expect(card).toContainText('Старый фрагмент');
    await r.page.locator('#material-update-button').click();
    await expect(card).toContainText('Новый контекст');
    await expect(card.getByRole('button',{name:'Весь фрагмент'})).toHaveAttribute('aria-expanded','false');
    await expect(r.page.locator('#versions')).toHaveCount(0);
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});

test('a replacement library cannot update the shown publication before its status is accepted',async()=>{
 const r=await reader();try{
  await r.page.addInitScript(()=>{
   const original=window.fetch.bind(window),state={enabled:false,checkHeld:false,publicationSeen:false,release:undefined as undefined|(()=>void)};
   Object.assign(window,{publicationRestoreRace:state});
   window.fetch=async(url,options)=>{
    const path=String(url);
    if(state.enabled&&['/api/v3/status','/api/v3/navigation'].includes(path)){
     const headers=new Headers(options?.headers);headers.delete('If-None-Match');
     const reply=await original(url,{...options,headers});
     const responseHeaders=new Headers(reply.headers);responseHeaders.set('X-Oko-Storage-Id','2d879353-68a3-4919-831c-32cb82c3d55f');
     const response=new Response(await reply.text(),{status:reply.status,headers:responseHeaders});
     if(path==='/api/v3/status'&&!state.checkHeld){state.checkHeld=true;await new Promise<void>(resolve=>{state.release=resolve;});}
     return response;
    }
    if(state.enabled&&path==='/api/v3/publication/A'){
     state.publicationSeen=true;
     const headers=new Headers(options?.headers);headers.delete('If-None-Match');
     const response=await original(url,{...options,headers}),body=await response.json();
     const newStorage='2d879353-68a3-4919-831c-32cb82c3d55f';
     if(new Headers(options?.headers).get('X-Oko-Storage-Id'))return new Response(JSON.stringify({code:'storage_changed',message:'Storage changed',details:null,diagnostic_id:null,command_id:null}),{status:409,headers:{'Content-Type':'application/json'}});
     body.filter={state:'ready',result:{hidden:true,reason:'NEW LIBRARY',text:'Original text',removed:[],provenance:{kind:'unknown',reason:'not_recorded'}},error:null};
     return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json',ETag:'"new-library"','X-Oko-Storage-Id':newStorage}});
    }
    return original(url,options);
   };
  });
  await r.start([material('A',{version:'same',text:'Original text'})]);
  await r.page.locator('[data-card="A"]').click();await expect(r.page.locator('#text')).toHaveText('Original text');
  await r.page.evaluate(()=>{(window as unknown as {publicationRestoreRace:{enabled:boolean}}).publicationRestoreRace.enabled=true;});
  await r.page.waitForFunction(()=>{const r=(window as unknown as {publicationRestoreRace:{checkHeld:boolean;publicationSeen:boolean}}).publicationRestoreRace;return r.checkHeld&&r.publicationSeen;});
  await expect(r.page.locator('#text')).toHaveText('Original text');
  await expect(r.page.locator('#filter-status')).not.toContainText('NEW LIBRARY');
  await r.page.evaluate(()=>{const r=(window as unknown as {publicationRestoreRace:{release?:()=>void}}).publicationRestoreRace;r.release?.();});
 }finally{await r.close();}
});

test('a late old-library publication response cannot cross an accepted storage reset',async()=>{
 const r=await reader();try{
  await r.page.addInitScript(()=>{
   const original=window.fetch.bind(window),state={enabled:false,reset:false,oldHeld:false,release:undefined as undefined|(()=>void)};
   Object.assign(window,{publicationLateRestore:state});
   const replacement='2d879353-68a3-4919-831c-32cb82c3d55f';
   window.fetch=async(url,options)=>{
    const path=String(url);
    if(state.enabled&&['/api/v3/status','/api/v3/navigation'].includes(path)){
     const headers=new Headers(options?.headers);headers.delete('If-None-Match');
     const reply=await original(url,{...options,headers});
     const responseHeaders=new Headers(reply.headers);responseHeaders.set('X-Oko-Storage-Id',replacement);
     if(path==='/api/v3/status')state.reset=true;
     return new Response(await reply.text(),{status:reply.status,headers:responseHeaders});
    }
    if(state.enabled&&path==='/api/v3/publication/A'){
     const identity=new Headers(options?.headers).get('X-Oko-Storage-Id'),headers=new Headers(options?.headers);headers.delete('If-None-Match');
     const response=await original(url,{...options,headers}),body=await response.json();
     const reason=identity===replacement?'NEW LIBRARY':'OLD LIBRARY';
     body.filter={state:'ready',result:{hidden:true,reason,text:'Original text',removed:[],provenance:{kind:'unknown',reason:'not_recorded'}},error:null};
     if(identity!==replacement&&!state.oldHeld){state.oldHeld=true;await new Promise<void>(resolve=>{state.release=resolve;});}
     return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json',ETag:'"'+reason+'"','X-Oko-Storage-Id':identity||response.headers.get('X-Oko-Storage-Id')!}});
    }
    return original(url,options);
   };
  });
  await r.start([material('A',{version:'same',text:'Original text'})]);
  await r.page.locator('[data-card="A"]').click();await expect(r.page.locator('#text')).toHaveText('Original text');
  await r.page.evaluate(()=>{(window as unknown as {publicationLateRestore:{enabled:boolean}}).publicationLateRestore.enabled=true;});
  await r.page.waitForFunction(()=>{const r=(window as unknown as {publicationLateRestore:{reset:boolean;oldHeld:boolean}}).publicationLateRestore;return r.reset&&r.oldHeld;});
  await r.page.evaluate(()=>{(window as unknown as {publicationLateRestore:{release?:()=>void}}).publicationLateRestore.release?.();});
  await expect(r.page.locator('#material-update-button')).toBeVisible();
  await expect(r.page.locator('#filter-status')).not.toContainText('OLD LIBRARY');
  await expect(r.page.locator('#text')).toHaveText('Original text');
  await r.page.locator('#material-update-button').click();
  await expect(r.page.locator('#filter-status')).toContainText('NEW LIBRARY');
 }finally{await r.close();}
});

test('processing and error states show the current attempt without inventing a retry schedule',async()=>{
 const r=await reader();try{
  await r.page.addInitScript(()=>{
   const original=window.fetch.bind(window),state={mode:'processing'};Object.assign(window,{publicationProcessingState:state});
   window.fetch=async(url,options)=>{
    if(String(url)!=='/api/v3/publication/A')return original(url,options);
    const headers=new Headers(options?.headers);headers.delete('If-None-Match');
    const response=await original(url,{...options,headers}),body=await response.json();
    body.theses={state:state.mode,result:null,error:state.mode==='error'?'Processor unavailable':null};
    return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json',ETag:'"'+state.mode+'"'}});
   };
  });
  await r.start([material('A')]);await r.page.locator('[data-card="A"]').click();
  await expect(r.page.locator('#ai-status')).toHaveText('Generating summary');
  await expect(r.page.locator('#theses-retry')).toBeHidden();
  await r.page.evaluate(()=>{(window as unknown as {publicationProcessingState:{mode:string}}).publicationProcessingState.mode='error';});
  await expect(r.page.locator('#ai-status')).toHaveText('Processor unavailable');
  await expect(r.page.locator('#theses-retry')).toBeVisible();
  await r.page.evaluate(()=>{(window as unknown as {publicationProcessingState:{mode:string}}).publicationProcessingState.mode='pending';});
  await expect(r.page.locator('#ai-status')).toHaveText('Waiting for processor');
  await expect(r.page.locator('#theses-retry')).toBeHidden();
 }finally{await r.close();}
});

test('active processing takes precedence over a previous diagnostic in the synthetic source',async()=>{
 const r=await reader();try{
  await r.start([material('A',{ai:{status:'running',error:'Previous failure'}})]);
  await r.page.locator('[data-card="A"]').click();
  await expect(r.page.locator('#ai-status')).toHaveText('Generating summary');
  await expect(r.page.locator('#theses-retry')).toBeHidden();
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});

test('a ready summary does not surface a previous diagnostic as an error',async()=>{
 const r=await reader();try{
  await r.start([material('A',{ai:{status:'done',error:'Previous failure'},theses:{points:[{text:'Ready summary',sources:[]}]}})]);
  await r.page.locator('[data-card="A"]').click();
  await expect(r.page.locator('#ai-status')).toHaveText('Summary ready');
  await expect(r.page.locator('#theses')).toHaveText('Ready summary');
  await expect(r.page.locator('#theses-retry')).toBeHidden();
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});
