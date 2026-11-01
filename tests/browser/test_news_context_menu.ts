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
 const original=(await api('cards')).items;const row=(id:string)=>page.locator('[data-card="'+id+'"]');const target=original[1],above=original[0],below=original[2];
 const open=original.find(c=>!c.loading&&c.id!==target.id)!;await row(open.id).click();await expect(page.locator('#title')).toHaveText(open.title);const title=open.title;
 await row(target.id).click({button:'right'});await expect(page.locator('#title')).toHaveText(title!);expect(await page.locator('#news-context-menu [role=menuitem]').count()).toBe(6);
 await page.keyboard.press('Escape');await expect(row(target.id)).toBeFocused();await row(target.id).press('Shift+F10');
 await page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).hover();await expect(page.locator('.news-range-preview')).toHaveCount(1);await page.screenshot({path:artifactPath('news-context-menu-a.png')});
 await page.getByRole('menuitem',{name:'Copy Link',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'Link copied'})).toBeVisible();expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(target.source_url);
 await context.route('**/article',route=>route.fulfill({contentType:'text/html',body:'<h1>Synthetic original</h1>'}));await row(target.id).click({button:'right'});const popupPromise=page.waitForEvent('popup');await page.getByRole('menuitem',{name:'Open in Browser',exact:true}).click();const popup=await popupPromise;await popup.waitForLoadState();expect(popup.url()).toBe(target.source_url);await popup.close();
 await row(target.id).click({button:'right'});await page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();await expect(page.locator('#news-read-dialog')).toContainText(above.title);await page.locator('#news-read-confirm').click();await expect.poll(async()=>((await api('cards')).items).length).toBe(2);expect(((await api('cards')).items).map(c=>c.id)).toEqual([target.id,below.id]);
 await row(target.id).click({button:'right'});await expect(page.getByRole('menuitem',{name:'Mark Above as Read',exact:true})).toBeDisabled();await page.getByRole('menuitem',{name:'Mark Below as Read',exact:true}).click();await page.locator('#news-read-confirm').click();await expect.poll(async()=>((await api('cards')).items).map(c=>c.id)).toEqual([target.id]);
 await row(target.id).click({button:'right'});await page.getByRole('menuitem',{name:'Mark as Read',exact:true}).click();await expect.poll(async()=>((await api('cards')).items).length).toBe(0);expect(errors).toEqual([]);
});

test('mark above clears the opened material only when it is in the range',async()=>{
 const r=await reader();try{
  await r.start(['a','b','c'].map(id=>material(id)));
  await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/materials/a/viewed',{body:{ok:true}}));
  await r.page.locator('[data-card="a"]').click();await expect(r.page.locator('#text')).toContainText('Текст a');
  await r.page.locator('[data-card="b"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();
  await r.page.evaluate(()=>{const t=readerTransport;const rows=t.replies.get('/api/v3/cards')!.body!;t.replies.set('/api/v3/cards',{body:rows.filter(c=>c.id!=='a')});});
  await r.page.locator('#news-read-confirm').click();
  await expect(r.page.locator('[data-card="a"]')).toHaveCount(0);await expect(r.page.locator('#material')).toBeHidden();await expect(r.page.locator('#empty')).toBeVisible();await expect(r.page.locator('#empty')).toHaveText('Select an item');await r.page.screenshot({path:artifactPath('news-range-cleared.png')});
  await r.page.locator('[data-card="c"]').click();await expect(r.page.locator('#text')).toContainText('Текст c');expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('range confirmation captures IDs before new arrivals and preserves target',async()=>{
 const r=await reader();try{
  await r.start(['a','b','c','d'].map(id=>material(id)));
  await r.page.evaluate(()=>{for(const id of ['a','b'])readerTransport.replies.set('/api/v3/materials/'+id+'/viewed',{body:{ok:true}});});
  await r.page.locator('[data-card="c"]').click();await r.page.locator('[data-card="c"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();
  await expect(r.page.locator('#news-read-dialog li')).toHaveCount(2);
  const fresh=material('fresh');await r.page.evaluate(fresh=>{const t=readerTransport;const rows=t.replies.get('/api/v3/cards')!.body!;t.replies.set('/api/v3/cards',{body:[fresh,...rows.filter(c=>c.id==='c'||c.id==='d')]});},fresh);
  await r.page.locator('#news-read-confirm').click();await expect(r.page.locator('#news-read-dialog')).toHaveCount(0);await expect(r.page.locator('[data-card="fresh"]')).toBeVisible();await expect(r.page.locator('#text')).toContainText('Текст c');
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url==='/api/v3/cards/read').map(q=>q.body))).toEqual([{ids:['a','b']}]);expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('failed batch reports an error and keeps all selected items',async()=>{
 const r=await reader();try{
  await r.start(['a','b','c','d'].map(id=>material(id)));
  await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/cards/read',{status:503,body:{code:'storage_unavailable',message:'Disk unavailable',details:{retry_at:null},command_id:null,diagnostic_id:null}}));
  await r.page.locator('[data-card="d"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark Above as Read',exact:true}).click();await r.page.locator('#news-read-confirm').click();
  await expect(r.page.locator('#news-read-dialog [role=status]')).toContainText('Retrying is safe');await expect(r.page.locator('#news-read-confirm')).toBeEnabled();
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(4);
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url==='/api/v3/cards/read').map(q=>q.body))).toEqual([{ids:['a','b','c']}]);await r.page.locator('#news-read-cancel').click();expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('news context menu reloads a full copy without marking it viewed',async()=>{
 const r=await reader();try{
  await r.start([material('reload')]);
  await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/materials/reload/retry',{body:{ok:true}}));
  await r.page.locator('[data-card="reload"]').click();
  await expect(r.page.locator('#text')).toContainText('Текст reload');
  await r.page.locator('[data-card="reload"]').click({button:'right'});
  await expect(r.page.getByRole('menuitem',{name:'Reload Publication',exact:true})).toBeVisible();
  await r.page.screenshot({path:artifactPath('telegram-reload-menu-desktop.png')});
  await r.page.getByRole('menuitem',{name:'Reload Publication',exact:true}).click();
  await expect.poll(()=>r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url.endsWith('/retry')).length)).toBe(1);
  await expect(r.page.locator('#news-context-menu')).toHaveCount(0);
  await expect(r.page.locator('#text')).toContainText('Текст reload');
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url.endsWith('/viewed')).length)).toBe(0);
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
