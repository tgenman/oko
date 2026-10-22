import type {Page} from 'playwright';
import {test} from 'node:test';
import {artifactPath} from './artifacts';
import {reader, material, expect} from './support.ts';
import assert from 'node:assert/strict';
import type {ReadingCheckResponse} from '../../frontend/src/api/generated/types.gen';

declare global {interface Window {replyVersions:Record<string,string>;}}

test('switch clears old material immediately and ignores late opening errors', async () => {
  const r = await reader();
  try {
    await r.start([material('A'), material('B'), material('C')]);
    await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
    await expect(r.page.locator('#text')).toHaveText('Текст A');
    await r.page.evaluate(() => { readerTransport.held.set('/api/v3/materials/B', true); readerTransport.held.set('/api/v3/materials/C', true); });
    await r.page.getByRole('button', {name: /^B/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/B'));
    await expect(r.page.locator('#text')).toHaveText('');
    await expect(r.page.locator('#empty')).toHaveText('Loading item…');
    await r.page.locator('#cards').getByRole('button', {name: /^C/}).click();
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
    await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
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
    await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
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
    await r.page.evaluate(() => readerTransport.replies.set('POST /api/v3/materials/A/viewed', {body:{ok:true},next:[['/api/v3/cards',{body:[{...readerTransport.replies.get('/api/v3/materials/B')!.body!}]}]]}));
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
    const unrelated = material('other', {folder_id: 'other-folder'});
    await r.start([source, unrelated]);
    const otherTab = await r.context.newPage(); await r.start([source], otherTab);
    await r.page.evaluate(() => readerTransport.held.set('/api/v3/materials/source', true));
    await r.page.getByRole('button', {name: /^source/}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/source'));
    await otherTab.getByRole('button', {name: /^source/}).click();
    await expect(otherTab.locator('#text')).toHaveText('Текст source');
    await otherTab.evaluate(() => readerTransport.replies.set('POST /api/v3/materials/source/viewed', {body:{ok:true},next:[['/api/v3/cards',{body:[]}]]}));
    await otherTab.getByRole('button', {name: 'Mark as Read', exact: true}).click();
    await expect(r.page.locator('#empty')).toHaveText('Select an item');
    await r.page.evaluate(body => readerTransport.release('/api/v3/materials/source', body), source);
    await settle(r.page);
    await expect(r.page.locator('#empty')).toHaveText('Select an item');
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
      await r.page.locator('#cards').getByRole('button', {name: /^A/}).click();
      await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/materials/A'));
      if (interruption === 'stop') await r.page.evaluate(()=>{readerTransport.replies.get('/api/v3/status')!.body!.stopped=true;});
      else {
        await r.page.evaluate(() => readerTransport.replies.set('/api/v3/status', {status: 503, body: {detail: 'Связь потеряна'}}));
      }
      await expect(r.page.locator('#empty')).toHaveText('Select an item');
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
  await r.page.evaluate(()=>{const t=readerTransport;t.held.set('/api/v3/materials/long',true);t.replies.get('/api/v3/cards')!.body![0].ai={status:'pending',error:'Запрещена публичная AI-обработка'};});
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/materials/long'));
  await expect(r.page.locator('#text')).toContainText('Длинный текст');
  expect(await r.page.locator('article').evaluate(node=>node.scrollTop)).toBe(before);
  await r.page.evaluate(()=>readerTransport.release('/api/v3/materials/long',readerTransport.replies.get('/api/v3/materials/long')!.body));
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
    await r.page.evaluate(fresh=>readerTransport.replies.set('/api/v3/materials/A',{body:fresh}),fresh);
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
    await r.page.evaluate(({next,png})=>{readerTransport.replies.set('/api/v3/materials/A/images/new.png',{base64:png,mime:'image/png'});readerTransport.replies.set('/api/v3/materials/A',{body:next});},{next,png});
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
    const filter=fallback==='filtered'?{status:'done' as const,hidden:false,restored:false,reason:'Фильтр',removed:['Реклама']}:undefined;
    const old=material('A',{text:lines.join('\n\n')+'\nРеклама',version:'old',...(filter?{filter}:{})});
    await r.start([old]);await r.page.locator('[data-card="A"]').click();
    const selector=fallback==='filtered'?'#filtered-text':'#text';
    await expect(r.page.locator(selector)).toContainText('Абзац 25');
    await r.page.locator(selector+' [data-reading-paragraph]').nth(25).evaluate(node=>node.scrollIntoView({block:'start'}));
    const anchor=await r.page.locator(selector+' [data-reading-paragraph]').nth(fallback==='neighbour'?24:25).boundingBox();
    const ratio=await r.page.locator('article').evaluate(node=>node.scrollTop/(node.scrollHeight-node.clientHeight));
    const nextLines=fallback==='relative'?lines.map(line=>line.replace('Абзац','Заменён')):lines.filter((_,i)=>fallback!=='neighbour'||i!==25);
    const fresh={...old,text:'Вставка\n\n'.repeat(8)+nextLines.join('\n\n')+(fallback==='relative'?'\nНовый конец':'\nРеклама'),version:'new'};
    await r.page.evaluate(fresh=>readerTransport.replies.set('/api/v3/materials/A',{body:fresh}),fresh);
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
      const fetch=window.fetch.bind(window);let reset=true;
      window.fetch=async(url,options)=>{
        if(String(url)!=='/api/v3/reading/check'||!readerTransport.held.has('test-storage-reset'))return fetch(url,options);
        const storage_id='2d879353-68a3-4919-831c-32cb82c3d55f';
        if(reset){reset=false;return new Response(JSON.stringify({state:'reset',storage_id}),{headers:{'Content-Type':'application/json'}});}
        const request=JSON.parse(String(options!.body));request.storage_id=null;
        const reply=await fetch(url,{...options,body:JSON.stringify(request)}),frame=await reply.json();
        return new Response(JSON.stringify({...frame,storage_id}),{headers:{'Content-Type':'application/json'}});
      };
    });
    await r.start([material('A',{version:'old',images:[url]})]);
    await r.page.evaluate(url=>readerTransport.held.set(url,true),url);
    await r.page.locator('[data-card="A"]').click();await expect(r.page.locator('#text')).toHaveText('Текст A');await r.page.waitForFunction(url=>readerTransport.pending.has(url),url);
    await r.page.evaluate(()=>{
      readerTransport.held.set('test-storage-reset',true);
      readerTransport.replies.set('/api/v3/materials/A',{body:{...readerTransport.replies.get('/api/v3/materials/A')!.body!,text:'Другой экземпляр',version:'old'}});
    });
    // Wait for the request after reset: the already in-flight pre-reset poll
    // can discover the new text before the injected reset is accepted.
    await r.page.waitForFunction(()=>readerTransport.requests.some(request=>request.url==='/api/v3/reading/check'&&Array.isArray(request.body?.resources)&&request.body.resources.some(resource=>resource.kind==='material'&&resource.known_version===null)));
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
  await r.start([material('A',{originals:false,title:'AI-агент Tolan, которым пользуются в основном женщины, поднял $30 млн',text:'Сегодня новостью про AI-агента сложно удивить. Но я сегодня погрузился [в историю от Upstarts Media про стартап Tolan](https://www.upstartsmedia.com/p/tolan-raises-30m-for-ai-companion-you-trust).\n\nОснователи Tolan — Квинтен Фармер, Аджай Мехта и Эван Голдшмидт. Их агент помогает с повседневными задачами.\n\nОн может запоминать, о чём с ним разговаривали, и поддерживать беседу на базе этих знаний.',previous:[{number:1,url:'/api/v3/materials/A/versions/1'},{number:2,url:'/api/v3/materials/A/versions/2'}]}),material('B')]);
  await r.page.locator('[data-card="A"]').click();
  await expect(r.page.locator('#text')).toBeVisible();
  assert.equal(await r.page.locator('#text').locator('xpath=ancestor::details').count(),0);
  await expect(r.page.locator('#original-panel')).toHaveCount(0);
  await expect(r.page.locator('#versions h2')).toHaveText('Previous Versions · 2');
  assert.deepEqual(await r.page.locator('#versions summary').allTextContents(),['Version 2','Version 1']);
  await expect(r.page.locator('#versions details[open]')).toHaveCount(0);
  await r.page.setViewportSize({width:1512,height:1000});
  await r.page.screenshot({path:artifactPath('current-and-history-desktop.png')});
  await r.page.setViewportSize({width:1120,height:1000});
  assert.equal(await r.page.locator('#text').evaluate(node=>node.scrollWidth<=node.clientWidth),true);
  assert.equal(await r.page.locator('#versions').evaluate(node=>node.scrollWidth<=node.clientWidth),true);
  await r.page.screenshot({path:artifactPath('current-and-history-narrow.png')});
  await r.page.evaluate(()=>{
   for(const number of [1,2])readerTransport.replies.set('/api/v3/materials/A/versions/'+number,{body:{title:'Прежняя версия',text:'Прежний текст',full:true,images:[],attachments:[]}});
  });
  await r.page.locator('#versions summary').first().focus();
  await r.page.keyboard.press('Enter');
  await expect(r.page.locator('#versions')).toContainText('Прежний текст');
  await r.page.locator('#versions summary').last().click();
  await expect(r.page.locator('#versions details[open]')).toHaveCount(2);
  await r.page.locator('#versions summary').first().click();
  await r.page.locator('#versions summary').first().click();
  await expect(r.page.locator('#versions')).toContainText('Прежний текст');
  await r.page.locator('[data-card="B"]').click();
  await expect(r.page.locator('#versions')).toBeEmpty();
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});

test('reply context is a compact separate card with safe navigation and local expansion', async () => {
  const r=await reader();
  const excerpt='Цитируемый фрагмент другого автора. '.repeat(20)+'Последние слова фрагмента.';
  try {
    await r.start([material('A',{reply_preview:{author:'Другой автор',text:excerpt,source_url:'https://t.me/fixture/10'}}),material('B')]);
    await r.page.evaluate(()=>{
      const validate=window.validateReaderReply;window.replyVersions={};
      window.validateReaderReply=async(url,method,status,body,alias,record)=>{
        await validate(url,method,status,body,alias,record);
        if(url==='/api/v3/reading/materials/A'){
          // The binding above has validated the delivered wire contract.
          const frame=body as ReadingCheckResponse;
          if(frame.state==='current')for(const resource of frame.resources){
            if(resource.id==='A'&&'version'in resource&&typeof resource.version==='string')window.replyVersions[resource.kind]=resource.version;
          }
        }
      };
    });
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
    // independent of the card and may run while the keyboard action completes.
    const requests=await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url!=='/api/v3/reading/check'||q.method!=='POST'));
    const start=await r.page.evaluate(()=>readerTransport.requests.length),versions=await r.page.evaluate(()=>window.replyVersions);
    assert.deepEqual(Object.keys(versions).sort(),['filter','material','media','theses']);
    await card.getByRole('button',{name:'Весь фрагмент'}).focus();
    await r.page.keyboard.press('Enter');
    await expect(card.getByRole('button',{name:'Свернуть'})).toHaveAttribute('aria-expanded','true');
    assert.equal(await card.locator('.reply-preview-text').textContent(),excerpt);
    await r.page.evaluate(async()=>{const reply=await fetch('/api/v3/reading/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({storage_id:null,resources:[]})});if(!reply.ok)throw new Error('Background check failed');});
    assert.deepEqual(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url!=='/api/v3/reading/check'||q.method!=='POST')),requests);
    const checks=await r.page.evaluate(start=>readerTransport.requests.slice(start).filter(q=>q.url==='/api/v3/reading/check'&&q.method==='POST'),start);
    for(const request of checks){
      assert.ok(Array.isArray(request.body?.resources));
      for(const resource of request.body.resources){
        if(['material','filter','theses','media'].includes(resource.kind)){
          assert.equal(resource.id,'A');assert.equal(resource.known_version,versions[resource.kind]);
        }
      }
    }
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
    const old=material('A',{version:'old',reply_preview:{author:null,text:'Старый фрагмент '.repeat(18),source_url:null},previous:[{number:1,url:'/api/v3/materials/A/versions/1'}]});
    await r.start([old]);await r.page.locator('[data-card="A"]').click();
    const card=r.page.locator('#reply-preview');
    await expect(card).toBeVisible();
    await expect(card.locator('strong')).toHaveText('Ответ на сообщение');
    await expect(card.locator('a')).toHaveCount(0);
    await card.getByRole('button',{name:'Весь фрагмент'}).click();
    const summarized={...old,theses:{points:[{text:'Новые тезисы той же версии',sources:[]}]}};
    await r.page.evaluate(value=>readerTransport.replies.set('/api/v3/materials/A',{body:value}),summarized);
    await expect(r.page.locator('#theses')).toHaveText('Новые тезисы той же версии');
    await expect(card.getByRole('button',{name:'Свернуть'})).toHaveAttribute('aria-expanded','true');
    const fresh={...summarized,version:'new',reply_preview:{author:'Другой автор',text:'Новый контекст',source_url:'https://t.me/fixture/20'}};
    await r.page.evaluate(value=>readerTransport.replies.set('/api/v3/materials/A',{body:value}),fresh);
    await expect(r.page.locator('#material-update-button')).toBeVisible();
    await expect(card).toContainText('Старый фрагмент');
    await r.page.locator('#material-update-button').click();
    await expect(card).toContainText('Новый контекст');
    await expect(card.getByRole('button',{name:'Весь фрагмент'})).toHaveAttribute('aria-expanded','false');
    await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/materials/A/versions/1',{body:{title:'История',text:'Старый собственный текст',full:true,images:[],attachments:[],reply_preview:{author:'Исторический автор',text:'Исторический фрагмент',source_url:'https://t.me/fixture/5'}}}));
    await r.page.locator('#versions summary').click();
    await expect(r.page.locator('#reply-version-1')).toContainText('Исторический фрагмент');
    assert.equal(await r.page.locator('#reply-version-1 a').getAttribute('href'),'https://t.me/fixture/5');
    assert.deepEqual(r.errors,[]);
  }finally{await r.close();}
});
