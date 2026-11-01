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
   t.replies.set('/api/v3/reading-composition',{body:{folders:[{id:'f',title:'Тема',count:2},{id:'new',title:'Новая',count:1}],feeds:[{id:'feed',title:'Источник',folder_id:'new',kind:'youtube',count:1},{id:'second',title:'second',folder_id:'f',kind:'rss',count:1}],count:2}});
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
  await expect(r.page.locator('[data-folder="new"]')).toHaveCount(0);
  await r.page.locator('#read-feeds-toggle').click();
  await expect(r.page.locator('[data-folder="new"]')).toHaveAttribute('aria-pressed','true');
  await r.page.evaluate(()=>{readerTransport.replies.get('/api/v3/reading-composition')!.body!.folders=[];});
  await expect(r.page.locator('#all-folders')).toHaveAttribute('aria-pressed','true');
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
  await r.page.locator('#read-feeds-toggle').click();
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

test('completed feeds toggle persists and keeps a selected completed feed until selection changes', async () => {
 const r=await reader();try {
  await r.start([material('one'),material('two',{feed_id:'second'})]);
  await r.page.evaluate(()=>{const t=readerTransport;const nav=t.replies.get('/api/v3/reading-composition')!.body!;
   if(!('feed' in nav.feeds[0]))nav.feeds[0].has_unviewed=true;
   nav.folders.push({id:'read-folder',title:'Архив',count:0});
   nav.feeds.push({id:'read',title:'Прочитанный',kind:'rss',folder_id:'read-folder',count:0,has_unviewed:false},{id:'hidden',title:'Скрытые материалы',kind:'rss',folder_id:'f',count:0,has_unviewed:true});
   t.replies.set('/api/v3/cards?sort=newest&feed=read',{body:[]});
   t.replies.set('/api/v3/cards?sort=newest&feed=feed',{body:[t.replies.get('/api/v3/cards')!.body![0]]});
  });
  await expect(r.page.locator('[data-feed="hidden"]')).toBeVisible();
  await expect(r.page.locator('[data-feed="read"]')).toHaveCount(0);
  await expect(r.page.locator('[data-folder="read-folder"]')).toHaveCount(0);
  const toggle=r.page.locator('#read-feeds-toggle');
  await expect(toggle).toHaveAttribute('aria-pressed','false');
  await toggle.click();
  await expect(r.page.locator('[data-feed="read"]')).toBeVisible();
  await r.page.reload();
  await expect(toggle).toHaveAttribute('aria-pressed','true');
  // Set up the read fixture again after reload, which recreates synthetic responses.
  await r.page.evaluate(()=>{const t=readerTransport;const nav=t.replies.get('/api/v3/reading-composition')!.body!;
   nav.feeds.push({id:'read',title:'Прочитанный',kind:'rss',folder_id:'f',count:0,has_unviewed:false});
   t.replies.set('/api/v3/cards?sort=newest&feed=read',{body:[]});
  });
  await r.page.locator('[data-feed="read"]').click();
  await expect(r.page.locator('#list-title')).toHaveText('Прочитанный');
  await toggle.click();
  await expect(r.page.locator('[data-feed="read"]')).toBeVisible();
  await expect(r.page.locator('[data-feed="read"]')).toHaveAttribute('aria-pressed','true');
  await r.page.locator('#all-folders').click();
  await expect(r.page.locator('[data-feed="read"]')).toHaveCount(0);
  await r.page.evaluate(()=>{const body=readerTransport.replies.get('/api/v3/reading-composition')!.body!;const entry=body.feeds.find(f=>f.id==='read');if(entry&&!('feed' in entry))entry.has_unviewed=true;});
  await expect(r.page.locator('[data-feed="read"]')).toBeVisible();
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});


test('feed avatars outside the viewport wait until the source is scrolled into view', async () => {
 const r=await reader();try {
  await r.page.setViewportSize({width:1280,height:700});
  await r.start([material('one',{feed_id:'feed-00'})]);
  await r.page.evaluate(()=>{
   const feeds=Array.from({length:60},(_,n)=>{const id='feed-'+String(n).padStart(2,'0');const avatar='/api/v3/feeds/'+id+'/avatar/'+'a'.repeat(64);
    readerTransport.replies.set(avatar,{base64:btoa('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><rect width="24" height="24" fill="red"/></svg>'),mime:'image/svg+xml'});
    return {count:1,has_unviewed:true,feed:{id,title:'Source '+String(n).padStart(2,'0'),kind:'telegram' as const,folder_id:'f',identity:id,importance:'normal' as const,connection:'ready' as const,history:{state:'absent' as const},initial_days:30,original_urls:[],public_ai:null,access:{kind:'web' as const,username:'fixture'+n,preview_url:'https://t.me/s/fixture'+n},avatar_url:avatar}};});
   readerTransport.replies.set('/api/v3/reading-composition',{body:{count:60,folders:[{id:'f',title:'Sources',position:0,count:60}],feeds}});
  });
  await expect(r.page.locator('[data-feed]')).toHaveCount(60);
  await expect(r.page.locator('[data-feed="feed-00"] img.source-avatar')).toBeVisible();
  expect(await r.page.evaluate(()=>readerTransport.requests.some(r=>r.url==='/api/v3/feeds/feed-59/avatar/'+'a'.repeat(64)))).toBe(false);
  await r.page.locator('[data-feed="feed-59"]').scrollIntoViewIfNeeded();
  await expect(r.page.locator('[data-feed="feed-59"] img.source-avatar')).toBeVisible();
  expect(await r.page.evaluate(()=>readerTransport.requests.some(r=>r.url==='/api/v3/feeds/feed-59/avatar/'+'a'.repeat(64)))).toBe(true);
  await r.page.screenshot({path:artifactPath('feed-avatar-lazy-scroll.png')});
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
