import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {reader,material,card} from './support';
import {artifactPath} from './artifacts';
import {addSources} from './connections';

test('reading shows the first page and loads the next on scroll', {timeout: 180000}, async t=>{
 t.diagnostic('Preparing synthetic library and starting reader');
 const r=await productionReader(t,{collection:false,library:{perFeed:26,active:false}});
 t.diagnostic('Reader started; checking initial page and scrolling');
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
  const items=Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published_at:new Date((1000-i)*1000).toISOString()}));
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
  const items=Array.from({length:30},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published_at:new Date((1000-i)*1000).toISOString()}));await r.start(items);
  await r.page.evaluate(rows=>window.readerFixtures.cardWindows(rows),items.map(card));
  await r.page.locator('[data-card="item-20"]').focus();
  const before=await r.page.locator('[data-card="item-20"]').evaluate(node=>node.getBoundingClientRect().top);
  await r.page.evaluate(()=>{const page=readerTransport.replies.get('/api/v3/cards')!.body!;page.items.unshift({...page.items[0],id:'fresh',title:'Fresh'});page.total=page.items.length;window.readerFixtures.cardWindows(page.items);});
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
  const items=Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published_at:new Date((1000-i)*1000).toISOString()}));
  await r.start(items);
  await r.page.evaluate(rows=>{window.readerFixtures.cardWindows(rows);readerTransport.replies.set('/api/v3/cards?sort=newest&cursor=item-40&limit=40',
   {body:{items:rows.slice(40),total:rows.length,next_cursor:null}});},items.map(card));
  await r.page.locator('[data-card="item-39"]').focus();
  await r.page.keyboard.press('ArrowDown');
  await expect(r.page.locator('[data-card="item-40"]')).toBeFocused();
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a late page does not move focus after the user chooses another card',async()=>{
 const r=await reader();try{
  const items=Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published_at:new Date((1000-i)*1000).toISOString()}));
  await r.start(items);
  await r.page.evaluate(rows=>{window.readerFixtures.cardWindows(rows);readerTransport.replies.set('/api/v3/cards?sort=newest&cursor=item-40&limit=40',
   {body:{items:rows.slice(40),total:rows.length,next_cursor:null}});},items.map(card));
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards?sort=newest&cursor=item-40&limit=40',true));
  await r.page.locator('[data-card="item-39"]').focus();await r.page.keyboard.press('ArrowDown');
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest&cursor=item-40&limit=40'));
  await r.page.locator('[data-card="item-02"]').focus();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards?sort=newest&cursor=item-40&limit=40',readerTransport.replies.get('/api/v3/cards?sort=newest&cursor=item-40&limit=40')!.body));
  await expect(r.page.locator('[data-card="item-40"]')).toHaveCount(1);
  await expect(r.page.locator('[data-card="item-02"]')).toBeFocused();expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a missing visible cursor restarts the current list',async()=>{
 const r=await reader();try{
  const items=Array.from({length:60},(_,i)=>material('item-'+String(i).padStart(2,'0'),{published_at:new Date((1000-i)*1000).toISOString()}));
  await r.start(items);
  await r.page.evaluate(rows=>{window.readerFixtures.cardWindows(rows);readerTransport.replies.set('/api/v3/cards?sort=newest&cursor=item-40&limit=40',
   {body:{items:rows.slice(40),total:rows.length,next_cursor:null}});},items.map(card));
  await r.page.locator('[data-card="item-20"]').focus();
  const first=await r.page.evaluate(()=>{const root=document.getElementById('card-scroll')!,bounds=root.getBoundingClientRect();return [...document.querySelectorAll<HTMLElement>('#cards [data-card]')].find(node=>{const rect=node.getBoundingClientRect();return rect.bottom>=bounds.top-150&&rect.top<=bounds.bottom+150;})!.dataset.card!;});
  await r.page.evaluate(id=>{
   const t=readerTransport,page=t.replies.get('/api/v3/cards')!.body!;
   const following=t.replies.get('/api/v3/cards?sort=newest&cursor=item-40&limit=40')!.body!;
   const items=[...page.items.filter(card=>card.id!==id),following.items[0]];
   t.replies.set('/api/v3/cards',{body:{items,total:59,next_cursor:following.items[1].id}});
   for(let limit=1;limit<=40;limit++)t.replies.set('/api/v3/cards?'+new URLSearchParams({sort:'newest',cursor:id,limit:String(limit)}),
    {status:409,body:{code:'cursor_expired',message:'Cursor expired',details:null,diagnostic_id:null,command_id:null}});
  },first);
  await expect(r.page.locator('#count')).toHaveText('59');
  await expect(r.page.locator('#card-scroll')).toHaveJSProperty('scrollTop',0);
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(40);
  await expect(r.page.locator(`[data-card="${first}"]`)).toHaveCount(0);expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a captured late page cannot undo a viewed action over real HTTP',{timeout:180000},async t=>{
 t.diagnostic('Preparing synthetic library and starting reader');
 const r=await productionReader(t,{collection:false,library:{perFeed:26,active:false}});
 t.diagnostic('Reader started; checking late page and viewed action');
 let release:(()=>void)|undefined;let captured=false;
 await r.page.route('**/api/v3/cards?*',async route=>{
  const query=new URL(route.request().url()).searchParams;
  if(!captured&&query.has('cursor')&&query.get('limit')==='40'){
   captured=true;const response=await route.fetch();await new Promise<void>(resolve=>{release=resolve;});await route.fulfill({response}).catch(()=>{});
  }else await route.continue().catch(()=>{});
 });
 await r.page.goto(r.origin+'/#token='+r.token);
 await expect(r.page.locator('#cards [data-card]')).toHaveCount(40);
 await r.page.locator('#card-scroll').evaluate(node=>{node.scrollTop=node.scrollHeight;});
 await expect.poll(()=>!!release,{timeout:15000}).toBe(true);
 await r.page.locator('#cards [data-card]').last().click();
 await expect(r.page.locator('#viewed')).toBeEnabled();await r.page.locator('#viewed').click();
 await expect(r.page.locator('#count')).toHaveText('103');
 release?.();
 await r.page.waitForTimeout(1800);
 await expect(r.page.locator('#count')).toHaveText('103');
});
