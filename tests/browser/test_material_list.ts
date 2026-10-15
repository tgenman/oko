import {test} from 'node:test';
import {reader, material, expect} from './support.ts';

test('compact rows show excerpts and current area with actions in menu', async () => {
 const r=await reader(); try {
  await r.start([material('video',{title:'Заголовок видео',snippet:'Описание сохранённого видео',feed_title:'Канал'}), material('article',{title:'Статья',snippet:'Текст статьи'})]);
  await expect(r.page.locator('#list-title')).toHaveText('Все материалы');
  await expect(r.page.locator('#count-label')).toHaveText('2 карточки');
  await expect(r.page.locator('#cards .card-snippet').first()).toHaveText('Описание сохранённого видео');
  await expect(r.page.locator('#cards .card-kind')).toHaveCount(0);
  await expect(r.page.locator('#cards .card-preview')).toHaveCount(2);
  await r.page.locator('[data-folder="f"]').click();
  await expect(r.page.locator('#list-title')).toHaveText('Тема');
  await expect(r.page.locator('#folder-viewed')).toBeVisible();
  await r.page.locator('#list-menu-toggle').click();
  await expect(r.page.locator('#compress,#digest-now')).toHaveCount(0);
  await r.page.keyboard.press('Escape');
  await r.page.locator('[data-feed="feed"]').click();
  await r.page.locator('#list-menu-toggle').click();
  await expect(r.page.locator('#compress,#digest-now')).toHaveCount(0);
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('keyboard focus does not open content and viewing focuses next without opening it', async () => {
 const r=await reader(); try {
  await r.start([material('one'),material('two'),material('three')]);
  await r.page.evaluate(() => {const t=readerTransport, [one,two,three]=t.replies.get('/api/v3/cards')!.body!;
   for (const [id, rows] of [['two',[one,three]], ['three',[one]], ['one',[]]])
    t.replies.set('POST /api/v3/materials/'+id+'/viewed', {body:{ok:true}, next:[['/api/v3/cards',{body:rows}]]});
  });
  await r.page.locator('[data-card="one"]').focus();
  await r.page.keyboard.press('ArrowDown');
  await expect(r.page.locator('[data-card="two"]')).toBeFocused();
  await expect(r.page.locator('#material')).toBeHidden();
  await r.page.keyboard.press('Enter');
  await expect(r.page.locator('#title')).toHaveText('two');
  await r.page.locator('#viewed').click();
  await expect(r.page.locator('[data-card="two"]')).toHaveCount(0);
  await expect(r.page.locator('[data-card="three"]')).toBeFocused();
  await expect(r.page.locator('#material')).toBeHidden();
  await r.page.keyboard.press('Enter');
  await expect(r.page.locator('#title')).toHaveText('three');
  await r.page.locator('#viewed').click();
  await expect(r.page.locator('[data-card="one"]')).toBeFocused();
  await r.page.keyboard.press('Enter');
  await r.page.locator('#viewed').click();
  await expect(r.page.locator('#list-status')).toHaveText('Нет материалов');
  await expect(r.page.locator('#list-menu-toggle')).toBeFocused();
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('date groups, saved sorting and displayed hidden card count use current rows', async () => {
 const r=await reader(); try {
  const now=Date.now()/1000;
  await r.start([material('today',{published:now}),material('yesterday',{published:now-86400}),material('unknown',{published:0,published_known:false})]);
  await expect(r.page.locator('.date-group')).toHaveText(['Сегодня','Вчера','Дата неизвестна']);
  await expect(r.page.locator('#count-label')).toHaveText('3 карточки');
  await r.page.evaluate(()=>{const t=readerTransport; t.replies.set('/api/v3/cards?sort=newest&show_hidden=true',{body:[t.replies.get('/api/v3/cards')!.body![0],{...t.replies.get('/api/v3/cards')!.body![0],id:'hidden',filter:{status:'done',hidden:true,restored:false,reason:'Синтетический фильтр',removed:[]}},...t.replies.get('/api/v3/cards')!.body!.slice(1)]});});
  await r.page.locator('#list-menu-toggle').click();
  await r.page.locator('#show-hidden').check();
  await expect(r.page.locator('#count-label')).toHaveText('4 карточки');
  await r.page.locator('#sort').selectOption('importance');
  await expect(r.page.locator('.date-group')).toHaveCount(0);
  await r.page.reload();
  await expect(r.page.locator('#sort')).toHaveValue('importance');
  await expect(r.page.locator('.date-group')).toHaveCount(0);
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('opened YouTube material preserves description separately from captions', async () => {
 const r=await reader(); try {
  await r.start([material('video',{description:'Описание из Atom',text:'Сохранённые субтитры',captions:'available'})]);
  await r.page.locator('[data-card="video"]').click();
  await expect(r.page.locator('#description')).toHaveText('Описание из Atom');
  await expect(r.page.locator('#text')).toHaveText('Сохранённые субтитры');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('current video description and previous captions remain available independently', async () => {
 const r=await reader(); try {
  await r.page.addInitScript(()=>readerTransport.replies.set('/api/v3/materials/video/versions/1',{body:{title:'Прежнее видео',text:'Прежние субтитры',full:true,reason:'',images:[],attachments:[]}}));
  await r.start([material('video',{description:'Текущее описание',text:'Текущие субтитры',previous:[{url:'/api/v3/materials/video/versions/1'}]})]);
  await r.page.locator('[data-card="video"]').click();
  await expect(r.page.locator('#title')).toHaveText('video');
  await expect(r.page.locator('#description')).toHaveText('Текущее описание');
  await r.page.locator('#versions summary').click();
  await expect(r.page.locator('#description')).toHaveText('Текущее описание');
  await expect(r.page.locator('#versions')).toContainText('Прежние субтитры');
  await expect(r.page.locator('#text')).toHaveText('Текущие субтитры');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('polling preserves row focus and anchored scroll, and returning restores area position', async () => {
 const r=await reader(); try {
  const items=Array.from({length:30},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:1000-i,feed_id:i%2?'second':'feed',snippet:'Краткий текст'}));
  await r.start(items);
  await r.page.evaluate(items => {const t=readerTransport;
   t.replies.set('/api/v3/cards?sort=newest&feed=feed',{body:[{...items[0],id:'new',title:'Новый материал',published:2000}, ...items]});
  }, items.filter((_,index)=>index%2===0).map(({text,...card})=>({feed_title:'feed',snippet:'',...card})));
  await r.page.locator('[data-card="item-20"]').focus();
  const before=await r.page.locator('[data-card="item-20"]').evaluate(n=>n.getBoundingClientRect().top);
  await r.page.evaluate(()=>readerTransport.replies.get('/api/v3/cards')!.body!.unshift({...readerTransport.replies.get('/api/v3/cards')!.body![0],id:'new',title:'Новый материал',published:2000}));
  await expect(r.page.locator('#cards button')).toHaveCount(31);
  await expect(r.page.locator('[data-card="item-20"]')).toBeFocused();
  expect(await r.page.locator('[data-card="item-20"]').evaluate(n=>n.getBoundingClientRect().top)).toBe(before);
  const allPosition=await r.page.locator('#card-scroll').evaluate(n=>n.scrollTop);
  await r.page.locator('[data-feed="feed"]').click();
  await expect(r.page.locator('#cards button')).toHaveCount(16);
  await expect(r.page.locator('#card-scroll')).toHaveJSProperty('scrollTop',0);
  await r.page.locator('#all-folders').click();
  await expect(r.page.locator('#cards button')).toHaveCount(31);
  expect(await r.page.locator('#card-scroll').evaluate(n=>n.scrollTop)).toBe(allPosition);
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('previews are reused during polling and late media cannot repopulate another area', async () => {
 const r=await reader(); try {
  await r.page.clock.install();
  const path='/api/v3/materials/one/images/image-0.png';
  await r.page.addInitScript(path=>readerTransport.replies.set(path,{mime:'image/png',base64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR9sAAAAASUVORK5CYII='}),path);
  await r.start([material('one',{images:[path],version:'1'}),material('two',{feed_id:'second'})]);
  await r.page.evaluate(() => {const t=readerTransport;
   t.replies.get('/api/v3/reading-composition')!.body!.feeds.push({id:'second',title:'second',folder_id:'f',kind:'rss',count:1});
   t.replies.set('/api/v3/cards?sort=newest&feed=second',{body:[t.replies.get('/api/v3/cards')!.body![1]]});
  });
  await expect(r.page.locator('[data-card="one"] img')).toHaveCount(1);
  const requests=await r.page.evaluate(path=>readerTransport.requests.filter(r=>r.url===path).length,path);
  for (let cycle=0;cycle<2;cycle++) {
    await r.page.evaluate(cycle=>{readerTransport.replies.get('/api/v3/cards')!.body![0].title='Poll '+cycle;},cycle);
    await r.page.clock.fastForward(500);
    await expect(r.page.locator('[data-card="one"] .card-title')).toHaveText('Poll '+cycle);
  }
  expect(await r.page.evaluate(path=>readerTransport.requests.filter(r=>r.url===path).length,path)).toBe(requests);
  await r.page.evaluate(path=>{readerTransport.held.set(path,true); readerTransport.replies.get('/api/v3/cards')!.body![0].version='2';},path);
  await r.page.waitForFunction(path=>readerTransport.pending.has(path),path);
  await r.page.locator('[data-feed="second"]').click();
  await expect(r.page.locator('#cards button')).toHaveCount(1);
  await r.page.evaluate(path=>readerTransport.release(path,{late:true}),path);
  await expect(r.page.locator('#cards img')).toHaveCount(0);
  const resources=await r.page.evaluate(()=>browserResources);
  expect(resources.revoked).toEqual(resources.created);
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('failed refresh keeps rows and retry updates current area, late failed selection is ignored', async () => {
 const r=await reader(); try {
  await r.start([material('one'),material('two',{feed_id:'second'})]);
  await r.page.evaluate(() => {const t=readerTransport;
   t.replies.get('/api/v3/reading-composition')!.body!.feeds.push({id:'second',title:'second',folder_id:'f',kind:'rss',count:1});
   t.replies.set('/api/v3/cards?sort=newest&feed=second',{body:[t.replies.get('/api/v3/cards')!.body![1]]});
  });
  await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/cards?sort=newest',{status:500,body:{detail:'Сбой списка'}}));
  await expect(r.page.locator('#list-status')).toContainText('Сбой списка');
  await expect(r.page.locator('#cards button')).toHaveCount(2);
  await expect(r.page.locator('#list-retry')).toBeVisible();
  await r.page.evaluate(()=>readerTransport.replies.delete('/api/v3/cards?sort=newest'));
  await r.page.locator('#list-retry').click();
  await expect(r.page.locator('#list-retry')).toBeHidden();
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards?sort=newest&feed=feed',true));
  await r.page.locator('[data-feed="feed"]').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest&feed=feed'));
  await expect(r.page.locator('#list-status')).toHaveText('Загрузка материалов…');
  await expect(r.page.locator('#cards button')).toHaveCount(0);
  await r.page.locator('[data-feed="second"]').click();
  await expect(r.page.locator('[data-card="two"]')).toBeVisible();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards?sort=newest&feed=feed',{detail:'Старый сбой'},500));
  await expect(r.page.locator('#list-retry')).toBeHidden();
  await expect(r.page.locator('#list-title')).toHaveText('second');
  await expect(r.page.locator('#cards button')).toHaveCount(1);
  await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
  await expect(r.page.locator('#debug-errors')).toContainText('Сбой списка');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('list width supports pointer and keyboard, persists, and shrinks to available space', async () => {
 const r=await reader(); try {
  await r.start([material('one')]);
  const divider=r.page.locator('#list-resize');
  await expect(divider).toHaveAttribute('aria-valuenow','440');
  await divider.focus(); await r.page.keyboard.press('ArrowRight');
  await expect(divider).toHaveAttribute('aria-valuenow','450');
  const box=await divider.boundingBox();
  await r.page.mouse.move(box!.x+2,box!.y+100); await r.page.mouse.down(); await r.page.mouse.move(box!.x+52,box!.y+100); await r.page.mouse.up();
  await expect(divider).toHaveAttribute('aria-valuenow','500');
  await r.page.reload(); await expect(divider).toHaveAttribute('aria-valuenow','500');
  await r.page.setViewportSize({width:1000,height:800});
  await expect(divider).toHaveAttribute('aria-valuenow','348');
  await r.page.setViewportSize({width:1280,height:720});
  await expect(divider).toHaveAttribute('aria-valuenow','500');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('incomplete materials and unavailable previews keep usable rows and diagnostic errors', async () => {
 const r=await reader(); try {
  await r.page.addInitScript(()=>readerTransport.replies.set('/api/v3/materials/broken/images/image-1.png',{status:500,body:{detail:'Превью недоступно'}}));
  await r.start([material('pending',{complete:false}),material('broken',{images:['/api/v3/materials/broken/images/image-1.png']})]);
  await expect(r.page.locator('[data-card="pending"] .card-kind')).toHaveText('Загружается');
  await expect(r.page.locator('[data-card="broken"] .card-preview')).toBeVisible();
  await expect(r.page.locator('[data-card="broken"] img')).toHaveCount(0);
  await r.page.locator('[data-card="broken"]').click();
  await expect(r.page.locator('#title')).toHaveText('broken');
  await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
  await expect(r.page.locator('#debug-errors')).toContainText('Не удалось загрузить превью');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});
