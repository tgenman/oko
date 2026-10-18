import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {reader,material} from './support';
import {artifactPath} from './artifacts';

test('news menu uses original links and marks only above/below in the displayed order', {timeout:30000},async t=>{
 const {page,context,api,origin,token}=await productionReader(t);await context.grantPermissions(['clipboard-read','clipboard-write']);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});await addSources(api,[['https://a.test/feed','Alpha','News'],['https://b.test/feed','Beta','News'],['https://source.test/feed','Gamma','Other']]);
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(origin+'/#token='+token);await expect(page.locator('#cards [data-card]')).toHaveCount(3);
 const original=await api('cards');const row=(id:string)=>page.locator('[data-card="'+id+'"]');const target=original[1],above=original[0],below=original[2];
 const open=original.find(c=>c.availability==='available'&&c.id!==target.id)!;await row(open.id).click();await expect(page.locator('#title')).toHaveText(open.title);const title=open.title;
 await row(target.id).click({button:'right'});await expect(page.locator('#title')).toHaveText(title!);expect(await page.locator('#news-context-menu [role=menuitem]').count()).toBe(5);
 await page.keyboard.press('Escape');await expect(row(target.id)).toBeFocused();await row(target.id).press('Shift+F10');
 await page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).hover();await expect(page.locator('.news-range-preview')).toHaveCount(1);await page.screenshot({path:artifactPath('news-context-menu-a.png')});
 await page.getByRole('menuitem',{name:'Copy Link',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'Link copied'})).toBeVisible();expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(target.source_url);
 await context.route('**/article',route=>route.fulfill({contentType:'text/html',body:'<h1>Synthetic original</h1>'}));await row(target.id).click({button:'right'});const popupPromise=page.waitForEvent('popup');await page.getByRole('menuitem',{name:'Open in Browser',exact:true}).click();const popup=await popupPromise;await popup.waitForLoadState();expect(popup.url()).toBe(target.source_url);await popup.close();
 await row(target.id).click({button:'right'});await page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();await expect(page.locator('#news-read-dialog')).toContainText(above.title);await page.locator('#news-read-confirm').click();await expect.poll(async()=>(await api('cards')).length).toBe(2);expect((await api('cards')).map(c=>c.id)).toEqual([target.id,below.id]);
 await row(target.id).click({button:'right'});await expect(page.getByRole('menuitem',{name:'Mark Above as Read',exact:true})).toBeDisabled();await page.getByRole('menuitem',{name:'Mark Below as Read',exact:true}).click();await page.locator('#news-read-confirm').click();await expect.poll(async()=>(await api('cards')).map(c=>c.id)).toEqual([target.id]);
 await row(target.id).click({button:'right'});await page.getByRole('menuitem',{name:'Mark as Read',exact:true}).click();await expect.poll(async()=>(await api('cards')).length).toBe(0);expect(errors).toEqual([]);
});

test('range confirmation captures IDs before new arrivals and preserves target',async()=>{
 const r=await reader();try{
  await r.start(['a','b','c','d'].map(id=>material(id)));
  await r.page.evaluate(()=>{for(const id of ['a','b'])readerTransport.replies.set('/api/v3/materials/'+id+'/viewed',{body:{ok:true}});});
  await r.page.locator('[data-card="c"]').click();await r.page.locator('[data-card="c"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();
  await expect(r.page.locator('#news-read-dialog li')).toHaveCount(2);
  const fresh=material('fresh');await r.page.evaluate(fresh=>{const t=readerTransport;const rows=t.replies.get('/api/v3/cards')!.body!;t.replies.set('/api/v3/cards',{body:[fresh,...rows.filter(c=>c.id==='c'||c.id==='d')]});},fresh);
  await r.page.locator('#news-read-confirm').click();await expect(r.page.locator('#news-read-dialog')).toHaveCount(0);await expect(r.page.locator('[data-card="fresh"]')).toBeVisible();await expect(r.page.locator('#text')).toContainText('Текст c');
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url.endsWith('/viewed')).map(q=>q.url))).toEqual(['/api/v3/materials/a/viewed','/api/v3/materials/b/viewed']);expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('stop a range during a held request prevents remaining commands and late UI changes',async()=>{
 const r=await reader();try{
  await r.start(['a','b','c'].map(id=>material(id)));
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/materials/a/viewed',true));
  await r.page.locator('[data-card="c"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();await r.page.locator('#news-read-confirm').click();await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/materials/a/viewed'));
  await r.page.locator('#news-read-cancel').click();await r.page.locator('[data-card="c"]').click();await r.page.locator('[data-card="c"]').click({button:'right'});
  await r.page.evaluate(async()=>{const channel=new BroadcastChannel('oko-reader');try{await new Promise<void>((resolve,reject)=>{const timer=window.setTimeout(()=>reject(new Error('No viewed receipt')),5000);channel.onmessage=e=>{if(e.data==='a'){window.clearTimeout(timer);resolve();}};readerTransport.release('/api/v3/materials/a/viewed',{ok:true});});}finally{channel.close();}});
  await expect(r.page.locator('#news-context-menu')).toBeVisible();await expect(r.page.locator('#text')).toContainText('Текст c');expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url.endsWith('/viewed')).length)).toBe(1);expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('range failure stops remaining writes and never retries the failed publication',async()=>{
 const r=await reader();try{
  await r.start(['a','b','c','d'].map(id=>material(id)));
  await r.page.evaluate(()=>{const t=readerTransport;t.replies.set('/api/v3/materials/a/viewed',{body:{ok:true}});t.replies.set('/api/v3/materials/b/viewed',{status:503,body:{code:'storage_unavailable',message:'Disk unavailable',details:{retry_at:null},command_id:null,diagnostic_id:null}});});
  await r.page.locator('[data-card="d"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();await r.page.locator('#news-read-confirm').click();await expect(r.page.locator('#news-read-dialog [role=status]')).toContainText('Completed 1 of 3');await expect(r.page.locator('#news-read-confirm')).toBeDisabled();
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url.endsWith('/viewed')).map(q=>q.url))).toEqual(['/api/v3/materials/a/viewed','/api/v3/materials/b/viewed']);await r.page.locator('#news-read-cancel').click();expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('new menu becomes available after a cancelled request fails late',async()=>{
 const r=await reader();try{
  await r.start(['a','b','c'].map(id=>material(id)));await r.page.evaluate(()=>readerTransport.held.set('/api/v3/materials/a/viewed',true));
  await r.page.locator('[data-card="c"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();await r.page.locator('#news-read-confirm').click();await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/materials/a/viewed'));await r.page.locator('#news-read-cancel').click();
  await r.page.locator('[data-folder="f"]').click();await r.page.locator('[data-card="c"]').click({button:'right'});await expect(r.page.getByRole('menuitem',{name:'Mark as Read',exact:true})).toBeDisabled();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/materials/a/viewed',{code:'storage_unavailable',message:'Late failure',details:{retry_at:null},command_id:null,diagnostic_id:null},503));
  await expect(r.page.getByRole('menuitem',{name:'Mark as Read',exact:true})).toBeEnabled();await expect(r.page.locator('#news-context-menu')).not.toContainText('Late failure');expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url.endsWith('/viewed')).length)).toBe(1);expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('above follows current sorting, visible hidden rows and feed selection',async()=>{
 const r=await reader();try{
  const a=material('a'),b=material('b'),c=material('c',{feed_id:'other'}),h=material('hidden');await r.start([a,b,c]);
  await r.page.evaluate(({a,b,c,h})=>{const t=readerTransport;t.replies.set('/api/v3/cards?sort=oldest',{body:[c,b,a]});t.replies.set('/api/v3/cards?sort=importance',{body:[b,a,c]});t.replies.set('/api/v3/cards?sort=newest&show_hidden=true',{body:[a,h,b,c]});t.replies.set('/api/v3/cards?sort=newest&feed=feed',{body:[a,b]});}, {a,b,c,h});
  const above=async(id:string,expected:string[])=>{await r.page.locator('[data-card="'+id+'"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();expect(await r.page.locator('#news-read-dialog li').allTextContents()).toEqual(expected);await r.page.locator('#news-read-cancel').click();};
  await above('b',['a']);await r.page.locator('#list-menu-toggle').click();await r.page.locator('#sort').selectOption('oldest');await expect(r.page.locator('#cards [data-card]').first()).toHaveAttribute('data-card','c');await above('b',['c']);
  await r.page.locator('#list-menu-toggle').click();await r.page.locator('#sort').selectOption('importance');await expect(r.page.locator('#cards [data-card]').first()).toHaveAttribute('data-card','b');await above('a',['b']);
  await r.page.locator('#list-menu-toggle').click();await r.page.locator('#sort').selectOption('newest');await r.page.locator('#show-hidden').check();await expect(r.page.locator('#cards [data-card]')).toHaveCount(4);await above('b',['a','hidden']);
  await r.page.locator('#list-menu-toggle').click();await r.page.locator('#show-hidden').uncheck();await r.page.locator('[data-feed="feed"]').click();await expect(r.page.locator('#cards [data-card]')).toHaveCount(2);await above('b',['a']);expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
