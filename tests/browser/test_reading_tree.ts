import {artifactPath} from './artifacts.ts';
import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {reader, material, expect} from './support.ts';

test('reading tree selects a feed and collapses folders without changing reading', async () => {
 const r = await reader(); try {
  await r.start([material('one'), material('two', {feed_id:'second'})]);
  await r.page.evaluate(() => {const t=readerTransport;
   t.replies.set('/api/v3/cards?sort=newest&feed=feed', {body:[t.replies.get('/api/v3/cards')!.body![0]]});
  });
  await expect(r.page.locator('#folders [data-feed="feed"]')).toBeVisible();
  await r.page.locator('#folders [data-feed="feed"]').click();
  await expect(r.page.locator('#cards button')).toHaveCount(1);
  await expect(r.page.locator('#folder-viewed')).toBeDisabled();
  await r.page.locator('[data-collapse="f"]').click();
  await expect(r.page.locator('#folders [data-feed="feed"]')).toBeHidden();
  await expect(r.page.locator('#cards button')).toHaveCount(1);
  await r.page.reload();
  await expect(r.page.locator('[data-collapse="f"]')).toHaveAttribute('aria-expanded','false');
 } finally {await r.close();}
});

test('tree keeps current choice across moves and deletion and rejects late cards', async () => {
 const r=await reader(); try {
  await r.start([material('one'),material('two',{feed_id:'second'})]);
  await r.page.evaluate(() => {const t=readerTransport;
   t.replies.set('/api/v3/cards?sort=newest&feed=feed', {body:[t.replies.get('/api/v3/cards')!.body![0]]});
  });
  await r.page.locator('[data-feed="feed"]').click();
  await expect(r.page.locator('#cards button')).toHaveCount(1);
  await r.page.evaluate(() => {
   const t=readerTransport;
   t.replies.set('/api/v3/reading-composition',{body:{folders:[{id:'f',title:'Тема',count:2},{id:'new',title:'Новая',count:1}],feeds:[{id:'feed',title:'Источник',folder_id:'new',kind:'youtube',count:1}],count:2}});
  });
  await expect(r.page.locator('[data-feed="feed"]')).toHaveAttribute('aria-pressed','true');
  await expect(r.page.locator('[data-collapse="new"]')).toHaveAttribute('aria-expanded','true');
  await r.page.evaluate(() => {readerTransport.held.set('/api/v3/cards?sort=newest&feed=feed',true);});
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest&feed=feed'));
  await r.page.locator('#all-folders').click();
  await expect(r.page.locator('#cards button')).toHaveCount(2);
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards?sort=newest&feed=feed',[{...readerTransport.replies.get('/api/v3/cards')!.body![0],id:'late',title:'Late reply'}]));
  await expect(r.page.locator('#cards')).not.toContainText('Late reply');
  await r.page.locator('[data-feed="feed"]').click();
  await r.page.evaluate(()=>{const n=readerTransport.replies.get('/api/v3/reading-composition')!.body;n!.feeds=[];});
  await expect(r.page.locator('[data-folder="new"]')).toHaveAttribute('aria-pressed','true');
  await r.page.evaluate(()=>{readerTransport.replies.get('/api/v3/reading-composition')!.body!.folders=[];});
  await expect(r.page.locator('#all-folders')).toHaveAttribute('aria-pressed','true');
 } finally {await r.close();}
});

test('width, focus, source warning and settings help remain separate from selection', async () => {
 const r=await reader(); try {
  await r.page.clock.install();
  await r.start([material('one')]);
  await r.page.evaluate(()=>{
   const t=readerTransport, feed={id:'feed',title:'Длинное название источника для проверки многоточия в дереве',folder_id:'f',kind:'youtube',transport:'youtube',count:1,original_urls:[],importance:'normal'};
   t.replies.set('/api/subscriptions',{body:{folders:[{id:'f',title:'Тема'}],feeds:[feed]}});
   t.replies.set('/api/v3/settings/feed/feed',{body:{...t.replies.get('/api/v3/settings/oko/global')!.body,feed}});
   t.replies.get('/api/status')!.body!.sources=[{feed_id:'feed',title:feed.title,error:'Нет связи',changes:'Справка синтетического источника'}];
  });
  await expect(r.page.locator('.tree-warning')).toBeVisible();
  await r.page.locator('.tree-warning').click();
  await expect(r.page.locator('#status-panel')).toBeVisible();
  await expect(r.page.locator('#all-folders')).toHaveAttribute('aria-pressed','true');
  await r.page.locator('#status-close').click();
  const divider=r.page.locator('#sidebar-resize');
  await divider.focus(); await r.page.keyboard.press('ArrowRight');
  await expect(divider).toHaveAttribute('aria-valuenow','290');
  await r.page.evaluate(()=>{readerTransport.replies.get('/api/v3/reading-composition')!.body!.count=7;});
  await r.page.clock.fastForward(500);
  await expect(r.page.locator('#all-folders .tree-count')).toHaveText('7');
  await expect(divider).toBeFocused();
  await r.page.locator('[data-feed="feed"]').dblclick();
  await expect(r.page.locator('#feeds-dialog')).toBeVisible();
  await feedOptions(r.page);
  await r.page.locator('#feed-options summary').click();
  await feedOptions(r.page);
  await expect(r.page.locator('#source-help')).toHaveText('Справка синтетического источника');
  await r.page.keyboard.press('Escape');
  await r.page.reload(); await expect(divider).toHaveAttribute('aria-valuenow','290');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('tree uses publication counts, alphabetical feeds and compact local icons', async () => {
 const r=await reader(); try {
  await r.page.clock.install();
  await r.start([material('one',{feed_id:'a',title:'Альфа'}),material('two',{feed_id:'b',title:'Бета'})]);
  await r.page.evaluate(()=>{
   readerTransport.replies.set('/api/v3/reading-composition',{body:{count:45,folders:[{id:'f',title:'Наука',count:45},{id:'empty',title:'Пустая',count:0}],feeds:[{id:'b',title:'Бета',kind:'telegram',folder_id:'f',count:20},{id:'a',title:'Альфа — очень длинное название синтетического YouTube-канала',kind:'youtube',folder_id:'f',count:25},{id:'c',title:'Вести',kind:'rss',folder_id:'f',count:0}]}});
  });
  await expect(r.page.locator('#all-folders .tree-count')).toHaveText('45');
  await expect(r.page.locator('[data-folder="empty"] .tree-count')).toHaveText('');
  await expect(r.page.locator('[data-feed]')).toHaveCount(3);
  expect(await r.page.locator('[data-feed]').evaluateAll(ns=>ns.map(n=>n.dataset.feed))).toEqual(['a','b','c']);
  await r.page.locator('[data-folder="f"]').click();
  await expect(r.page.locator('#count')).toHaveText('2');
  await expect(r.page.locator('#cards')).toContainText('Альфа');
  await r.page.locator('#list-menu-toggle').click();
  await r.page.locator('#show-hidden').check();
  await expect(r.page.locator('[data-folder="f"] .tree-count')).toHaveText('45');
  const clipped=await r.page.locator('[data-feed="a"] .tree-title').evaluate(n=>n.scrollHeight>n.clientHeight);
  expect(clipped).toBe(true);
  await r.page.screenshot({path:artifactPath('feed-sidebar.png')});
  await r.page.locator('[data-feed="a"]').focus();
  await r.page.evaluate(()=>{readerTransport.replies.get('/api/v3/reading-composition')!.body!.count=46;});
  await r.page.clock.fastForward(500);
  await expect(r.page.locator('#all-folders .tree-count')).toHaveText('46');
  await expect(r.page.locator('[data-feed="a"]')).toBeFocused();
  await r.page.keyboard.press('Enter');
  await expect(r.page.locator('[data-feed="a"]')).toHaveAttribute('aria-pressed','true');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('unavailable local storage does not block reading or resizing', async () => {
 const r=await reader(); try {
  await r.page.addInitScript(()=>{Storage.prototype.setItem=()=>{throw new Error('Storage unavailable');};});
  await r.start([material('one')]);
  await r.page.locator('[data-collapse="f"]').click();
  await expect(r.page.locator('[data-collapse="f"]')).toHaveAttribute('aria-expanded','false');
  await r.page.locator('#sidebar-resize').focus(); await r.page.keyboard.press('ArrowRight');
  await expect(r.page.locator('#sidebar-resize')).toHaveAttribute('aria-valuenow','290');
  expect(r.errors).toEqual([]);
 } finally {await r.close();}
});

test('refresh keeps tree scroll and pointer resizing uses saved width', async () => {
 const r=await reader(); try {
  await r.start([material('one')]);
  await r.page.evaluate(()=>{readerTransport.replies.set('/api/v3/reading-composition',{body:{count:1,folders:[{id:'f',title:'Тема',count:1}],feeds:Array.from({length:40},(_,i)=>({id:'source-'+i,title:'Источник '+String(i).padStart(2,'0'),folder_id:'f',kind:'rss',count:i===39?1:0}))}});});
  await expect(r.page.locator('[data-feed]')).toHaveCount(40);
  await r.page.locator('[data-feed="source-39"]').focus();
  const scroll=await r.page.locator('#reading-nav').evaluate(n=>n.scrollTop);
  expect(scroll).toBeGreaterThan(0);
  await r.page.evaluate(()=>{readerTransport.replies.get('/api/v3/reading-composition')!.body!.count=2;});
  await expect(r.page.locator('#all-folders .tree-count')).toHaveText('2');
  await expect(r.page.locator('[data-feed="source-39"]')).toBeFocused();
  expect(await r.page.locator('#reading-nav').evaluate(n=>n.scrollTop)).toBe(scroll);
  const edge=await r.page.locator('#sidebar-resize').boundingBox();
  await r.page.mouse.move(edge!.x+2,edge!.y+100); await r.page.mouse.down(); await r.page.mouse.move(340,edge!.y+100); await r.page.mouse.up();
  await expect(r.page.locator('#sidebar-resize')).toHaveAttribute('aria-valuenow','340');
  await r.page.reload(); await expect(r.page.locator('#sidebar-resize')).toHaveAttribute('aria-valuenow','340');
 } finally {await r.close();}
});
