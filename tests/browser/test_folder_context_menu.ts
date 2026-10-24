import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {reader,material} from './support';
import {artifactPath} from './artifacts';

test('folder menu A preserves selection and supports settings, rename, read all and empty-only deletion',{timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','Science'],['https://b.test/feed','Beta','Personal']]);
 const science=catalog.folders.find(f=>f.title==='Science')!,personal=catalog.folders.find(f=>f.title==='Personal')!,alpha=catalog.feeds.find(f=>f.title==='Alpha')!;
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/#token='+token);await expect(page.locator('#cards button')).toHaveCount(2);
 const row=(id:string)=>page.locator('#folders [data-folder="'+id+'"]');
 await row(personal.id).click();await page.locator('#cards button').click();await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await page.locator('[data-collapse="'+science.id+'"]').click();
 await row(science.id).click({button:'right'});await expect(row(personal.id)).toHaveAttribute('aria-pressed','true');
 expect(await page.locator('#folder-context-menu > button').allTextContents()).toEqual(['↻Refresh','✓Mark All as Read','⚙Settings…','✎Rename…','⌫Delete…']);
 await page.screenshot({path:artifactPath('folder-menu-a.png')});
 await page.keyboard.press('End');await expect(page.getByRole('menuitem',{name:'Delete…',exact:true})).toBeFocused();
 await page.keyboard.press('Escape');await expect(row(science.id)).toBeFocused();
 await row(science.id).press('Shift+F10');await page.getByRole('menuitem',{name:'Settings…',exact:true}).click();
 await expect(page.locator('#owner-heading')).toHaveText('Folder: Science');await page.locator('#feeds-close').click();
 await row(science.id).click({button:'right'});await page.getByRole('menuitem',{name:'Delete…',exact:true}).click();
 await expect(page.locator('#folder-action-heading')).toHaveText('Folder Is Not Empty');
 await page.screenshot({path:artifactPath('folder-menu-nonempty.png')});await page.getByRole('button',{name:'OK',exact:true}).click();
 expect((await api('subscriptions')).feeds).toHaveLength(2);
 await row(science.id).click({button:'right'});await page.getByRole('menuitem',{name:'Rename…',exact:true}).click();
 await expect(page.locator('#folder-rename-name')).toHaveValue('Science');
 await page.locator('#folder-rename-name').fill('Personal');await page.locator('#folder-action-confirm').click();await expect(page.locator('#folder-action-message')).toContainText('already exists');
 await page.locator('#folder-rename-name').fill('  Research  ');await page.locator('#folder-rename-name').press('Enter');await expect(page.locator('#folder-action-dialog')).toHaveCount(0);
 await expect(row(science.id)).toHaveAttribute('aria-label','Research');await expect(page.locator('[data-collapse="'+science.id+'"]').first()).toHaveAttribute('aria-expanded','false');
 await expect(row(personal.id)).toHaveAttribute('aria-pressed','true');await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await row(science.id).click({button:'right'});await page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();await expect(page.locator('#completion-heading')).toContainText('Research');await expect(page.locator('#completion-confirm')).toBeEnabled();await page.locator('#completion-confirm').click();
 await expect.poll(async()=>(await api('cards')).length).toBe(1);await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await page.locator('#read-feeds-toggle').click();await row(science.id).click({button:'right'});await page.getByRole('menuitem',{name:'Delete…',exact:true}).click();await expect(page.locator('#folder-action-heading')).toHaveText('Folder Is Not Empty');await page.locator('#folder-action-close').click();
 await api('feeds/'+alpha.id,'PATCH',{folder_id:personal.id});
 await row(science.id).click();await row(science.id).click({button:'right'});await page.getByRole('menuitem',{name:'Delete…',exact:true}).click();await expect(page.locator('#folder-action-heading')).toHaveText('Delete Folder');await page.locator('#folder-action-cancel').click();
 await row(science.id).click({button:'right'});await page.getByRole('menuitem',{name:'Delete…',exact:true}).click();await page.locator('#folder-action-confirm').click();await expect(row(science.id)).toHaveCount(0);await expect(page.locator('#all-folders')).toHaveAttribute('aria-pressed','true');
 expect((await api('subscriptions')).feeds).toHaveLength(2);expect(errors).toEqual([]);
});

test('folder deletion rechecks an empty folder when a feed arrives before confirmation',{timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','A'],['https://b.test/feed','Beta','B']]);
 const a=catalog.folders.find(f=>f.title==='A')!,b=catalog.folders.find(f=>f.title==='B')!,alpha=catalog.feeds.find(f=>f.title==='Alpha')!;
 await api('feeds/'+alpha.id,'PATCH',{folder_id:b.id});
 await page.goto(origin+'/#token='+token);await page.locator('#read-feeds-toggle').click();
 const row=page.locator('[data-folder="'+a.id+'"]');await row.click({button:'right'});await page.getByRole('menuitem',{name:'Delete…',exact:true}).click();await expect(page.locator('#folder-action-heading')).toHaveText('Delete Folder');
 let release!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 // Keep the browser's empty-folder snapshot until the real DELETE rechecks it.
 await page.route('**/api/v3/reading-composition',async route=>{await held;await route.continue();});
 try{
  await api('feeds/'+alpha.id,'PATCH',{folder_id:a.id});
  const rejected=page.waitForResponse(response=>response.url().endsWith('/folders/'+a.id)&&response.request().method()==='DELETE');
  await page.locator('#folder-action-confirm').click();expect((await rejected).status()).toBe(409);
  await expect(page.locator('#folder-action-heading')).toHaveText('Folder Is Not Empty');
 }finally{release();}
 expect((await api('subscriptions')).folders.some(f=>f.id===a.id)).toBe(true);await page.locator('#folder-action-close').click();
});

test('late folder rename reconciles its write without closing a newer folder dialog',{timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','A'],['https://b.test/feed','Beta','B']]);
 const a=catalog.folders.find(f=>f.title==='A')!,b=catalog.folders.find(f=>f.title==='B')!;
 await page.goto(origin+'/#token='+token);await expect(page.locator('#cards button')).toHaveCount(2);
 let release!:()=>void,committed=false;const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 await page.route('**/api/v3/folders/'+a.id,async route=>{const response=await route.fetch();committed=true;await held;await route.fulfill({response});});
 await page.locator('[data-folder="'+a.id+'"]').click({button:'right'});await page.getByRole('menuitem',{name:'Rename…',exact:true}).click();await page.locator('#folder-rename-name').fill('Renamed A');await page.locator('#folder-action-confirm').click();await expect.poll(()=>committed).toBe(true);
 await page.locator('#folder-action-cancel').click();await page.locator('[data-folder="'+b.id+'"]').click({button:'right'});await page.getByRole('menuitem',{name:'Rename…',exact:true}).click();await page.locator('#folder-rename-name').fill('New B draft');release();
 await expect(page.locator('[data-folder="'+a.id+'"]').first()).toHaveAttribute('aria-label','Renamed A');await expect(page.locator('#folder-rename-name')).toHaveValue('New B draft');await expect(page.locator('#folder-action-confirm')).toBeEnabled();await page.locator('#folder-action-cancel').click();
});

test('folder completion by context menu includes hidden zero-count items and ignores a late preview',async()=>{
 const r=await reader();try{
  await r.start([material('a')]);
  await r.page.evaluate(()=>{
   readerTransport.replies.set('/api/v3/reading-composition',{body:{count:1,folders:[{id:'f',title:'Hidden',count:0},{id:'other',title:'Other',count:1}],feeds:[{id:'feed',title:'feed',folder_id:'f',kind:'rss',count:0,has_unviewed:true},{id:'other-feed',title:'other',folder_id:'other',kind:'rss',count:1,has_unviewed:true}]}});
   readerTransport.held.set('/api/v3/folders/f/completion/preview',true);
   readerTransport.replies.set('/api/v3/folders/other/completion/preview',{body:{token:'other',cards_count:1,publications_count:1}});
  });
  await expect(r.page.locator('[data-folder=other]')).toBeVisible();
  await r.page.locator('[data-folder=f]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/folders/f/completion/preview'));
  await r.page.locator('#completion-close').click();await r.page.locator('[data-folder=other]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/folders/f/completion/preview',{token:'old',cards_count:0,publications_count:4}));
  await expect(r.page.locator('#completion-heading')).toContainText('Other');await expect(r.page.locator('#completion-count')).toContainText('Original publications: 1');await expect(r.page.locator('#completion-confirm')).toBeEnabled();
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
