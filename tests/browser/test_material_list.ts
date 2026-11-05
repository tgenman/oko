import {openAdmin,openReading} from './admin-navigation';
import {test} from 'node:test';
import {reader, material, card, expect} from './support.ts';

test('compact rows show titles without post excerpts and current area with actions in menu', async () => {
 const r=await reader(); try {
  await r.start([material('video',{title:'Заголовок видео',feed_title:'Канал'}), material('article',{title:'Статья'})]);
  await expect(r.page.locator('#list-title')).toHaveText('All Items');
  await expect(r.page.locator('#count-label')).toHaveText('2');
  await expect(r.page.locator('#cards .card-snippet')).toHaveCount(0);
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
  await r.page.evaluate(() => {const t=readerTransport, [one,two,three]=t.replies.get('/api/v3/cards')!.body!.items;
   for (const [id, rows] of ([['two',[one,three]], ['three',[one]], ['one',[]]] as const))
    t.replies.set('POST /api/v3/materials/'+id+'/viewed', {body:{ok:true}, next:[['/api/v3/cards',{body:window.readerFixtures.cardPage([...rows])}]]});
  });
  await r.page.locator('[data-card="one"]').focus();
  await r.page.keyboard.press('ArrowDown');
  await expect(r.page.locator('[data-card="two"]')).toBeFocused();
  await expect(r.page.locator('#material')).toBeHidden();
  await r.page.keyboard.press('Enter');
  await expect(r.page.locator('#title')).toHaveText('two');
  await openReading(r.page);await r.page.locator('#viewed').click();
  await expect(r.page.locator('[data-card="two"]')).toHaveCount(0);
  await expect(r.page.locator('[data-card="three"]')).toBeFocused();
  await expect(r.page.locator('#text')).toHaveText('Текст two');
  await expect(r.page.locator('#material-retained')).toBeVisible();
  await r.page.keyboard.press('Enter');
  await expect(r.page.locator('#title')).toHaveText('three');
  await openReading(r.page);await r.page.locator('#viewed').click();
  await expect(r.page.locator('[data-card="one"]')).toBeFocused();
  await r.page.keyboard.press('Enter');
  await openReading(r.page);await r.page.locator('#viewed').click();
  await expect(r.page.locator('#list-status')).toHaveText('No items');
  await expect(r.page.locator('#list-menu-toggle')).toBeFocused();
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('date groups, saved sorting and displayed hidden card count use current rows', async () => {
 const r=await reader(); try {
  const now=Date.now()/1000;
  const input=[material('today',{published_at:new Date(now*1000).toISOString()}),material('yesterday',{published_at:new Date((now-86400)*1000).toISOString()}),material('unknown',{published_at:null})];
  const rows=input.map(card),withHidden=[rows[0],{...rows[0],id:'hidden',hidden:true},...rows.slice(1)];
  const hiddenPage={items:withHidden,total:withHidden.length,next_cursor:null};
  await r.start(input,undefined,{'/api/v3/cards?sort=newest&show_hidden=true':hiddenPage,'/api/v3/cards?sort=oldest&show_hidden=true':hiddenPage,'/api/v3/cards?sort=oldest':hiddenPage});
  await expect(r.page.locator('.date-group')).toHaveText(['Today','Yesterday','Unknown date']);
  await expect(r.page.locator('#count-label')).toHaveText('3');
  await r.page.locator('#list-menu-toggle').click();
  await r.page.locator('#show-hidden').check();
  await expect(r.page.locator('#count-label')).toHaveText('4');
  await r.page.locator('#sort').selectOption('oldest');
  await expect(r.page.locator('.date-group')).toHaveCount(3);
  await r.page.reload();
  await expect(r.page.locator('#sort')).toHaveValue('oldest');
  await expect(r.page.locator('.date-group')).toHaveCount(3);
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('opened YouTube material preserves description separately from captions', async () => {
 const r=await reader(); try {
  await r.start([material('video',{description:'Описание из Atom',text:'Сохранённые субтитры',source:{kind:'youtube',feed_id:'feed',source_url:'https://example.org/video',captions:'available'}})]);
  await r.page.locator('[data-card="video"]').click();
  await expect(r.page.locator('#description')).toHaveText('Описание из Atom');
  await expect(r.page.locator('#text p').last()).toHaveText('Сохранённые субтитры');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('polling preserves row focus and anchored scroll, and returning restores area position', async () => {
 const r=await reader(); try {
  const items=Array.from({length:30},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published_at:new Date((1000-i)*1000).toISOString(),feed_id:i%2?'second':'feed'}));
  await r.start(items);
  await r.page.evaluate(rows=>window.readerFixtures.cardWindows(rows),items.map(card));
  await r.page.evaluate(items => {const t=readerTransport;
   t.replies.set('/api/v3/cards?sort=newest&feed=feed',{body:window.readerFixtures.cardPage([{...items[0],id:'new',title:'Новый материал',published_at:new Date(2000*1000).toISOString()}, ...items])});
  }, items.filter((_,index)=>index%2===0).map(card));
  await r.page.locator('[data-card="item-20"]').focus();
  const before=await r.page.locator('[data-card="item-20"]').evaluate(n=>n.getBoundingClientRect().top);
  await r.page.evaluate(()=>{const page=readerTransport.replies.get('/api/v3/cards')!.body!;page.items.unshift({...page.items[0],id:'new',title:'Новый материал',published_at:new Date(2000*1000).toISOString()});page.total=page.items.length;window.readerFixtures.cardWindows(page.items);});
  await expect(r.page.locator('#cards-new')).toBeVisible();
  await expect(r.page.locator('#cards button')).toHaveCount(30);
  await expect(r.page.locator('[data-card="item-20"]')).toBeFocused();
  expect(await r.page.locator('[data-card="item-20"]').evaluate(n=>n.getBoundingClientRect().top)).toBe(before);
  const allPosition=await r.page.locator('#card-scroll').evaluate(n=>n.scrollTop);
  await r.page.locator('[data-feed="feed"]').click();
  await expect(r.page.locator('#cards button')).toHaveCount(16);
  await expect(r.page.locator('#card-scroll')).toHaveJSProperty('scrollTop',0);
  await r.page.locator('#all-folders').click();
  await expect(r.page.locator('#cards button')).toHaveCount(30);
  expect(await r.page.locator('#card-scroll').evaluate(n=>n.scrollTop)).toBe(allPosition);
  await r.page.locator('#cards-new').click();
  await expect(r.page.locator('#cards button')).toHaveCount(31);
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('failed refresh keeps rows and retry updates current area, late failed selection is ignored', async () => {
 const r=await reader(); try {
  await r.start([material('one'),material('two',{feed_id:'second'})]);
  await r.page.locator('[data-card="one"]').click();await expect(r.page.locator('#text')).toContainText('Текст one');
  await r.page.evaluate(() => {const t=readerTransport;
   const folder=t.replies.get('/api/v3/navigation')!.body!.folders[0];folder.feeds=folder.feeds.filter(f=>f.id!=='second').concat(window.readerFixtures.navigationFeed({id:'second',title:'second',count:1}));
   t.replies.set('/api/v3/cards?sort=newest&feed=second',{body:window.readerFixtures.cardPage([t.replies.get('/api/v3/cards')!.body!.items[1]])});
  });
  await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/cards?sort=newest',{status:500,body:{detail:'Сбой списка'}}));
  await expect(r.page.locator('#list-status')).toContainText('Сбой списка');
  await expect(r.page.locator('#cards button')).toHaveCount(2);
  await expect(r.page.locator('#list-retry')).toBeVisible();
  await expect(r.page.locator('#stop')).toBeEnabled();await expect(r.page.locator('#text')).toContainText('Текст one');
  await r.page.evaluate(()=>document.getElementById('list-retry')!.addEventListener('click',()=>readerTransport.replies.delete('/api/v3/cards?sort=newest'),{capture:true,once:true}));
  await r.page.locator('#list-retry').click();
  await expect(r.page.locator('#list-retry')).toBeHidden();
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards?sort=newest&feed=feed',true));
  await r.page.locator('[data-feed="feed"]').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest&feed=feed'));
  await expect(r.page.locator('#list-status')).toHaveText('Loading items…');
  await expect(r.page.locator('#cards button')).toHaveCount(0);
  await r.page.locator('[data-feed="second"]').click();
  await expect(r.page.locator('[data-card="two"]')).toBeVisible();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards?sort=newest&feed=feed',{detail:'Старый сбой'},500));
  await expect(r.page.locator('#list-retry')).toBeHidden();
  await expect(r.page.locator('#list-title')).toHaveText('second');
  await expect(r.page.locator('#cards button')).toHaveCount(1);
  await openAdmin(r.page);await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
  await expect(r.page.locator('#debug-errors')).toContainText('Сбой списка');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('news titles show link labels without Markdown destinations', async () => {
 const r=await reader(); try {
  const longTitle='Дополнительный текст заголовка. '.repeat(15).trim();
  const cases=[
   ['[Bloomberg](https://example.test/news/'+('long-path-'.repeat(25))+'): '+longTitle,'Bloomberg: '+longTitle],
   ['У [Kai-Fu Lee](https://en.wikipedia.org/wiki/Kai-Fu_Lee) вышла книга (+ [интервью](https://t.me/example/123))','У Kai-Fu Lee вышла книга (+ интервью)'],
   ['[Статья](https://example.test/wiki/Topic_(detail)) и [обзор](https://example.test/review "Подсказка")','Статья и обзор'],
   ['Начало [интервью](https://t.me/pr','Начало интервью'],
   ['Обычный заголовок [часть 1]','Обычный заголовок [часть 1]'],
  ];
  await r.start(cases.map(([title],i)=>material('link-'+i,{title,text:'Текст [ссылка](https://example.test/body)'})));
  for(const [i,[,title]] of cases.entries()) {
   const row=r.page.locator(`[data-card="link-${i}"]`);
   await expect(row.locator('.card-title')).toHaveText(title!);
   await expect(row.locator('.card-title')).toHaveAttribute('title',title!);
   await expect(row).toHaveAttribute('aria-label',new RegExp(title!.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
   await row.click();
   await expect(r.page.locator('#title')).toHaveText(title!);
   await expect(r.page.locator('#title a')).toHaveAttribute('href',new RegExp('^https?://'));
   await expect(r.page.locator('#text')).toHaveText('Текст ссылка');
   await expect(r.page.locator('#text').getByRole('link',{name:'ссылка',exact:true})).toHaveAttribute('href','https://example.test/body');
  }
  await r.page.locator('[data-card="link-0"]').click();
  await expect(r.page.locator('#title')).toHaveText(cases[0]![1]!);
  await expect(r.page.locator('#completeness')).toHaveText('Fully saved');
  await r.page.screenshot({path:'/tmp/oko-title-links.png',fullPage:true});
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});
