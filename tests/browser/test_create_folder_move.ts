import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';

test('new destination dialog creates and moves a feed, validates and cancels without changing reading', {timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','Science'],['https://b.test/feed','Beta','Science']]);
 const alpha=catalog.feeds.find(f=>f.title==='Alpha')!,beta=catalog.feeds.find(f=>f.title==='Beta')!;
 const row=(id:string)=>page.locator('#folders [data-feed="'+id+'"]');
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/#token='+token);await expect(row(beta.id)).toBeVisible();
 await row(beta.id).click();await page.locator('#cards button').click();await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await row(alpha.id).press('Shift+F10');await page.getByRole('menuitem',{name:'Move to Folder',exact:true}).focus();await page.keyboard.press('ArrowRight');await page.keyboard.press('End');
 await expect(page.getByRole('menuitem',{name:'New Folder…',exact:true})).toBeFocused();await page.keyboard.press('Enter');
 await expect(page.locator('#feed-new-folder-name')).toBeFocused();await expect(page.locator('#feed-action-heading')).toHaveText('New Folder');
 await page.locator('#feed-action-confirm').click();await expect(page.locator('#feed-new-folder-message')).toHaveText('Enter a folder name.');
 await page.locator('#feed-new-folder-name').fill(' Science ');await page.locator('#feed-action-confirm').click();await expect(page.locator('#feed-new-folder-message')).toContainText('already exists');
 await page.locator('#feed-new-folder-name').fill('Discard');await page.keyboard.press('Escape');await expect(row(alpha.id)).toBeFocused();
 expect((await api('subscriptions')).folders).toEqual(catalog.folders);expect((await api('subscriptions')).feeds.map(f=>[f.id,f.folder_id])).toEqual(catalog.feeds.map(f=>[f.id,f.folder_id]));
 async function open(){await row(alpha.id).click({button:'right'});await page.getByRole('menuitem',{name:'Move to Folder',exact:true}).hover();await page.getByRole('menuitem',{name:'New Folder…',exact:true}).click();}
 await open();await page.locator('#feed-new-folder-name').fill('Cancel this');await page.locator('#feed-action-cancel').click();expect((await api('subscriptions')).folders).toEqual(catalog.folders);expect((await api('subscriptions')).feeds.map(f=>[f.id,f.folder_id])).toEqual(catalog.feeds.map(f=>[f.id,f.folder_id]));
 await open();await page.locator('#feed-new-folder-name').fill(' Cosmos ');
 await page.screenshot({path:artifactPath('create-folder-a-dialog.png')});
 await page.locator('#feed-new-folder-name').press('Enter');await expect(page.locator('#feed-action-dialog')).toHaveCount(0);
 await expect.poll(async()=>(await api('subscriptions')).folders.some(f=>f.title==='Cosmos')).toBe(true);
 const after=await api('subscriptions'),folder=after.folders.find(f=>f.title==='Cosmos')!;
 expect(after.feeds.find(f=>f.id===alpha.id)?.folder_id).toBe(folder.id);
 await expect(row(alpha.id)).toBeVisible();
 await expect(row(beta.id)).toHaveAttribute('aria-pressed','true');await expect(page.locator('#text')).toContainText('Открылась библиотека');
 expect(after.feeds.find(f=>f.id===beta.id)?.folder_id).toBe(beta.folder_id);expect(errors).toEqual([]);
});

test('late create-and-move reconciles data without closing a newer dialog', {timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','Science'],['https://b.test/feed','Beta','Science']]);
 const alpha=catalog.feeds.find(f=>f.title==='Alpha')!,beta=catalog.feeds.find(f=>f.title==='Beta')!;
 const row=(id:string)=>page.locator('#folders [data-feed="'+id+'"]');
 await page.goto(origin+'/#token='+token);await expect(row(alpha.id)).toBeVisible();
 let release!:()=>void,committed=false;const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 await page.route('**/api/v3/feeds/'+alpha.id,async route=>{const response=await route.fetch();committed=true;await held;await route.fulfill({response});});
 async function open(id:string){await row(id).click({button:'right'});await page.getByRole('menuitem',{name:'Move to Folder',exact:true}).hover();await page.getByRole('menuitem',{name:'New Folder…',exact:true}).click();}
 await open(alpha.id);await page.locator('#feed-new-folder-name').fill('Cosmos');await page.locator('#feed-action-confirm').click();await expect.poll(()=>committed).toBe(true);await expect(page.locator('#feed-action-confirm')).toBeDisabled();
 await page.locator('#feed-action-cancel').click();await open(beta.id);await page.locator('#feed-new-folder-name').fill('Keep my draft');release();
 await expect(page.locator('#feed-new-folder-name')).toHaveValue('Keep my draft');await expect(page.locator('#feed-new-folder-help')).toContainText('Beta');await expect(page.locator('#feed-action-confirm')).toBeEnabled();
 await expect.poll(async()=>(await api('subscriptions')).folders.some(f=>f.title==='Cosmos')).toBe(true);
 await page.locator('#feed-action-cancel').click();
});

test('reopening the same feed during creation becomes usable when the pending command settles', {timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','Science']]);const alpha=catalog.feeds[0]!;
 const row=page.locator('#folders [data-feed="'+alpha.id+'"]');
 await page.goto(origin+'/#token='+token);await expect(row).toBeVisible();
 let release!:()=>void,committed=false;const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 await page.route('**/api/v3/feeds/'+alpha.id,async route=>{const response=await route.fetch();committed=true;await held;await route.fulfill({response});});
 await row.click({button:'right'});await page.getByRole('menuitem',{name:'Move to Folder',exact:true}).hover();await page.getByRole('menuitem',{name:'New Folder…',exact:true}).click();
 await page.locator('#feed-new-folder-name').fill('Cosmos');await page.locator('#feed-action-confirm').click();await expect.poll(()=>committed).toBe(true);await page.locator('#feed-action-cancel').click();
 await row.click({button:'right'});await expect(page.getByRole('menuitem',{name:'Move to Folder',exact:true})).toBeDisabled();release();
 await expect(page.getByRole('menuitem',{name:'Move to Folder',exact:true})).toBeEnabled();await expect(page.locator('#feed-context-menu')).toBeVisible();
 await page.getByRole('menuitem',{name:'Move to Folder',exact:true}).hover();await page.getByRole('menuitem',{name:'New Folder…',exact:true}).click();await expect(page.locator('#feed-new-folder-name')).toBeEnabled();
});

test('server name conflict keeps the draft and a lost response cannot repeat creation', {timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','Science'],['https://b.test/feed','Beta','Science']]);
 const alpha=catalog.feeds.find(f=>f.title==='Alpha')!,beta=catalog.feeds.find(f=>f.title==='Beta')!;
 const row=page.locator('#folders [data-feed="'+alpha.id+'"]');
 await page.goto(origin+'/#token='+token);await expect(row).toBeVisible();
 let requests=0;
 await page.route('**/api/v3/feeds/'+alpha.id,async route=>{requests++;if(requests===1){await api('feeds/'+beta.id,'PATCH',{new_folder:'Raced'});await route.continue();}else{await route.fetch();await route.abort('failed');}});
 await row.click({button:'right'});await page.getByRole('menuitem',{name:'Move to Folder',exact:true}).hover();await page.getByRole('menuitem',{name:'New Folder…',exact:true}).click();
 await page.locator('#feed-new-folder-name').fill('Raced');await page.locator('#feed-action-confirm').click();await expect(page.locator('#feed-new-folder-message')).toContainText('already exists');await expect(page.locator('#feed-new-folder-name')).toHaveValue('Raced');
 expect((await api('subscriptions')).feeds.find(f=>f.id===alpha.id)?.folder_id).toBe(alpha.folder_id);
 await page.locator('#feed-new-folder-name').fill('Accepted');await page.locator('#feed-action-confirm').click();
 await expect(page.locator('#feed-new-folder-message')).toContainText('Action outcome unknown');await expect(page.locator('#feed-action-confirm')).toBeDisabled();await expect(page.locator('#feed-new-folder-name')).toBeDisabled();
 expect(requests).toBe(2);expect((await api('subscriptions')).folders.filter(f=>f.title==='Accepted')).toHaveLength(1);
});
