import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {reader,material} from './support';
import {artifactPath} from './artifacts';

test('reading shows the first page and loads the next on scroll', {timeout: 45000}, async t=>{
 const r=await productionReader(t,{collection:false,library:{perFeed:26,active:false}});
 const requests:{search:string;etag:string|undefined}[]=[];
 r.page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/api/v3/cards')requests.push({search:url.search,etag:request.headers()['if-none-match']});});
 await r.page.goto(r.origin+'/#token='+r.token);
 await expect(r.page.locator('#count')).toHaveText('104');
 await expect(r.page.locator('#cards [data-card]')).toHaveCount(40);
 expect(requests.length).toBeGreaterThan(0);
 await r.page.locator('#card-scroll').evaluate(n=>{n.scrollTop=n.scrollHeight;});
 await expect(r.page.locator('#cards [data-card]')).toHaveCount(80);
 expect(requests.some(request=>new URLSearchParams(request.search).has('cursor'))).toBe(true);
 await expect.poll(()=>requests.some(request=>!!request.etag&&new URLSearchParams(request.search).has('cursor')),{timeout:8000}).toBe(true);
 expect(requests.every(request=>Number(new URLSearchParams(request.search).get('limit'))<=40)).toBe(true);
});


test('Above and Below send only cards the user has seen, excluding the anchor',async()=>{
 const r=await reader();try{
  const items=Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:1000-i}));
  await r.start(items);
  await r.page.locator('[data-card="item-01"]').click({button:'right'});
  await r.page.getByRole('menuitem',{name:'Mark Below as Read',exact:true}).click();
  const names=await r.page.locator('#news-read-dialog li').allTextContents();
  expect(names.length).toBeGreaterThan(0);expect(names.length).toBeLessThan(38);
  expect(names).not.toContain('item-01');expect(names).not.toContain('item-39');expect(names).not.toContain('item-59');
  await r.page.locator('#news-read-confirm').click();
  await expect.poll(()=>r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url==='/api/v3/cards/read').length)).toBe(1);
  const sent=await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url==='/api/v3/cards/read').map(q=>q.body));
  expect(sent).toEqual([{ids:names}]);expect(r.errors).toEqual([]);
 }finally{await r.close();}
});


test('new arrivals show a notice while the user is scrolled down',async()=>{
 const r=await reader();try{
  const items=Array.from({length:30},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:1000-i}));await r.start(items);
  await r.page.locator('[data-card="item-20"]').focus();
  const before=await r.page.locator('[data-card="item-20"]').evaluate(node=>node.getBoundingClientRect().top);
  await r.page.evaluate(()=>{const rows=readerTransport.replies.get('/api/v3/cards')!.body!;rows.unshift({...rows[0],id:'fresh',title:'Fresh'});});
  await expect(r.page.locator('#cards-new')).toBeVisible();
  await expect(r.page.locator('#cards-new')).toBeInViewport();
  await r.page.screenshot({path:artifactPath('new-cards-notice.png')});
  expect(await r.page.locator('[data-card="item-20"]').evaluate(node=>node.getBoundingClientRect().top)).toBe(before);
  await expect(r.page.locator('[data-card="fresh"]')).toHaveCount(0);
  await r.page.locator('#cards-new').click();await expect(r.page.locator('[data-card="fresh"]')).toBeVisible();expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('ArrowDown at the end of a page loads and focuses the next card',async()=>{
 const r=await reader();try{
  await r.start(Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:1000-i})));
  await r.page.locator('[data-card="item-39"]').focus();
  await r.page.keyboard.press('ArrowDown');
  await expect(r.page.locator('[data-card="item-40"]')).toBeFocused();
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a late page does not move focus after the user chooses another card',async()=>{
 const r=await reader();try{
  await r.start(Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:1000-i})));
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards?sort=newest',true));
  await r.page.locator('[data-card="item-39"]').focus();await r.page.keyboard.press('ArrowDown');
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest'));
  await r.page.locator('[data-card="item-02"]').focus();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards?sort=newest',readerTransport.replies.get('/api/v3/cards')!.body));
  await expect(r.page.locator('[data-card="item-40"]')).toHaveCount(1);
  await expect(r.page.locator('[data-card="item-02"]')).toBeFocused();expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a missing visible cursor restarts the current list',async()=>{
 const r=await reader();try{
  await r.start(Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:1000-i})));
  await r.page.locator('[data-card="item-20"]').focus();
  const first=await r.page.evaluate(()=>{const root=document.getElementById('card-scroll')!,bounds=root.getBoundingClientRect();return [...document.querySelectorAll<HTMLElement>('#cards [data-card]')].find(node=>{const rect=node.getBoundingClientRect();return rect.bottom>=bounds.top-150&&rect.top<=bounds.bottom+150;})!.dataset.card!;});
  await r.page.evaluate(id=>{const rows=readerTransport.replies.get('/api/v3/cards')!.body!;readerTransport.replies.set('/api/v3/cards',{body:rows.filter(card=>card.id!==id)});},first);
  await expect(r.page.locator('#count')).toHaveText('59');
  await expect(r.page.locator('#card-scroll')).toHaveJSProperty('scrollTop',0);
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(40);
  await expect(r.page.locator(`[data-card="${first}"]`)).toHaveCount(0);expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('refreshing the first page keeps previously loaded cards beyond its boundary',async()=>{
 const r=await reader();try{
  await r.start(Array.from({length:85},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:1000-i})));
  await r.page.locator('#card-scroll').evaluate(node=>{node.scrollTop=node.scrollHeight;});
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(80);
  await r.page.locator('#card-scroll').evaluate(node=>{node.scrollTop=0;});
  await r.page.evaluate(()=>{const rows=readerTransport.replies.get('/api/v3/cards')!.body!;rows.unshift({...rows[0],id:'fresh',title:'Fresh'});});
  await expect(r.page.locator('#count')).toHaveText('86');
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(81);
  await expect(r.page.locator('[data-card="item-39"]')).toHaveCount(1);
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('new arrivals are announced while browsing oldest first',async()=>{
 const r=await reader();try{
  await r.start(Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:i+1})));
  await r.page.evaluate(()=>{const t=readerTransport;t.replies.set('/api/v3/cards?sort=oldest',{body:[...t.replies.get('/api/v3/cards')!.body!].reverse()});});
  await r.page.locator('#list-menu-toggle').click();await r.page.locator('#sort').selectOption('oldest');
  await expect(r.page.locator('#cards [data-card]').first()).toHaveAttribute('data-card','item-59');
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(40);
  await r.page.locator('#card-scroll').evaluate(node=>{node.scrollTop=node.scrollHeight;});
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(60);
  await r.page.locator('[data-card="item-00"]').focus();
  await r.page.evaluate(()=>{const rows=readerTransport.replies.get('/api/v3/cards?sort=oldest')!.body!;rows.push({...rows[0],id:'fresh',title:'Fresh'});});
  await expect(r.page.locator('#cards-new')).toBeInViewport();
  await expect(r.page.locator('#count')).toHaveText('61');expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a held page from the previous area does not block loading the next area',async()=>{
 const r=await reader();try{
  await r.start(Array.from({length:80},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published:1000-i})));
  await r.page.evaluate(()=>{const t=readerTransport;t.replies.set('/api/v3/cards?sort=newest&feed=feed',{body:t.replies.get('/api/v3/cards')!.body});t.held.set('/api/v3/cards?sort=newest',true);});
  await r.page.locator('#card-scroll').evaluate(node=>{node.scrollTop=node.scrollHeight;});
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest'));
  await r.page.locator('[data-feed="feed"]').click();await expect(r.page.locator('#list-title')).toHaveText('feed');
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(40);
  await r.page.locator('#card-scroll').evaluate(node=>{node.scrollTop=node.scrollHeight;});
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(80);
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards?sort=newest',readerTransport.replies.get('/api/v3/cards')!.body));
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
