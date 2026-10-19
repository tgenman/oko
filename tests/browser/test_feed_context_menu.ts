import {test} from 'node:test';
import {reader,material} from './support';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';

test('feed context menu supports settings, nested folder move, read all and confirmed local deletion', {timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','Science'],['https://b.test/feed','Beta','Science'],['https://source.test/feed','Gamma','Personal']]);
 const alpha=catalog.feeds.find(f=>f.title==='Alpha')!,beta=catalog.feeds.find(f=>f.title==='Beta')!,personal=catalog.folders.find(f=>f.title==='Personal')!;
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/#token='+token);await expect(page.locator('#cards button')).toHaveCount(3);
 const row=(id:string)=>page.locator('#folders [data-feed="'+id+'"]');
 await row(beta.id).click();await page.locator('#cards button').click();await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await row(alpha.id).click({button:'right'});await expect(row(beta.id)).toHaveAttribute('aria-pressed','true');
 expect(await page.locator('#feed-context-menu > button').allTextContents()).toEqual(['✓Mark All as Read','⚙Settings…','⌫Delete…']);
 await page.keyboard.press('Escape');await expect(row(alpha.id)).toBeFocused();await expect(page.locator('#feed-context-menu')).toHaveCount(0);
 await row(alpha.id).press('Shift+F10');await page.getByRole('menuitem',{name:'Settings…',exact:true}).click();
 await expect(page.locator('#owner-heading')).toContainText('Alpha');await expect(page.locator('#feeds-dialog')).toBeVisible();await page.locator('#feeds-close').click();
 await row(alpha.id).click({button:'right'});await page.getByRole('menuitem',{name:'Move to Folder',exact:true}).hover();
 await expect(page.getByRole('menuitemradio',{name:'Science',exact:true})).toHaveAttribute('aria-checked','true');
 await page.screenshot({path:artifactPath('feed-context-menu-a-folders.png')});
 await page.getByRole('menuitemradio',{name:'Personal',exact:true}).click();
 await expect.poll(async()=>(await api('subscriptions')).feeds.find(f=>f.id===alpha.id)?.folder_id).toBe(personal.id);
 await expect(row(beta.id)).toHaveAttribute('aria-pressed','true');await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await row(alpha.id).click({button:'right'});await page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();await expect(page.locator('#feed-action-confirm')).toBeEnabled();
 await page.locator('#feed-action-confirm').click();await expect(page.locator('#feed-action-dialog')).toHaveCount(0);
 await expect.poll(async()=>(await api('cards')).length).toBe(2);await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await page.locator('#read-feeds-toggle').click();await row(alpha.id).click({button:'right'});await expect(page.getByRole('menuitem',{name:'Mark All as Read',exact:true})).toBeDisabled();await page.keyboard.press('Escape');
 await row(beta.id).click({button:'right'});await page.getByRole('menuitem',{name:'Delete…',exact:true}).click();await page.locator('#feed-action-cancel').click();expect((await api('subscriptions')).feeds.some(f=>f.id===beta.id)).toBe(true);
 await row(beta.id).click({button:'right'});await page.getByRole('menuitem',{name:'Delete…',exact:true}).click();await page.locator('#feed-action-confirm').click();
 await expect.poll(async()=>(await api('subscriptions')).feeds.some(f=>f.id===beta.id)).toBe(false);await expect(row(beta.id)).toHaveCount(0);
 expect((await api('subscriptions')).feeds.some(f=>f.id===alpha.id)).toBe(true);expect(errors).toEqual([]);
});

test('late feed completion cannot close a newer dialog or clear a sibling material', {timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api,[['https://a.test/feed','Alpha','Science'],['https://b.test/feed','Beta','Science']]);
 const alpha=catalog.feeds.find(f=>f.title==='Alpha')!,beta=catalog.feeds.find(f=>f.title==='Beta')!;
 await page.goto(origin+'/#token='+token);await expect(page.locator('#cards button')).toHaveCount(2);
 let release!:()=>void;let committed=false;const held=new Promise<void>(resolve=>{release=resolve;});
 await page.route('**/api/v3/feeds/'+alpha.id+'/completion',async route=>{const response=await route.fetch();committed=true;await held;await route.fulfill({response});});
 const row=(id:string)=>page.locator('#folders [data-feed="'+id+'"]');
 await row(alpha.id).click({button:'right'});await page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();await expect(page.locator('#feed-action-confirm')).toBeEnabled();await page.locator('#feed-action-confirm').click();await expect.poll(()=>committed).toBe(true);
 // Server completion may already remove Alpha from the tree; only its response is held.
 if(await page.locator('#feed-action-cancel').count())await page.locator('#feed-action-cancel').click();
 await row(beta.id).click();await page.locator('#cards button').click();await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await row(beta.id).click({button:'right'});await page.getByRole('menuitem',{name:'Delete…',exact:true}).click();release();
 await expect(page.locator('#feed-action-heading')).toContainText('Delete Feed · Beta');await expect(page.locator('#text')).toContainText('Открылась библиотека');await expect(page.locator('#feed-action-confirm')).toBeEnabled();
 await page.locator('#feed-action-cancel').click();
});


test('late completion response preserves a new publication opened in the same feed',async()=>{
 const r=await reader();try{
  await r.start([material('old')]);
  await r.page.evaluate(()=>{const t=readerTransport;t.replies.set('/api/v3/feeds/feed/completion/preview',{body:{token:'captured',cards_count:1,publications_count:1}});t.held.set('/api/v3/feeds/feed/completion',true);});
  await r.page.locator('[data-feed="feed"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();await expect(r.page.locator('#feed-action-confirm')).toBeEnabled();await r.page.locator('#feed-action-confirm').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/feeds/feed/completion'));
  await r.page.locator('#feed-action-cancel').click();
  const fresh=material('fresh');
  await r.page.evaluate(fresh=>{readerTransport.replies.set('/api/v3/cards',{body:[fresh]});readerTransport.replies.set('/api/v3/materials/fresh',{body:fresh});},fresh);
  await expect(r.page.locator('[data-card="fresh"]')).toBeVisible();await r.page.locator('[data-card="fresh"]').click();await expect(r.page.locator('#text')).toContainText('Текст fresh');
  await r.page.evaluate(async()=>{const channel=new BroadcastChannel('oko-reader');try{await new Promise<void>((resolve,reject)=>{const timer=window.setTimeout(()=>reject(new Error('Completion response was not processed')),5000);channel.onmessage=event=>{if(event.data?.kind==='feed-viewed'&&event.data.feed==='feed'){window.clearTimeout(timer);resolve();}};readerTransport.release('/api/v3/feeds/feed/completion',{ok:true,command_id:'completed-old'});});}finally{channel.close();}});
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url==='/api/v3/feeds/feed/completion').length)).toBe(1);
  await expect(r.page.locator('#text')).toContainText('Текст fresh');await expect(r.page.locator('[data-card="fresh"]')).toBeVisible();expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('unknown feed completion disables the old confirmation and reopening requires a fresh preview',async()=>{
 const r=await reader();try{
  await r.start([material('a')]);
  await r.page.evaluate(()=>{const t=readerTransport;t.replies.set('/api/v3/feeds/feed/completion/preview',{body:{token:'first',cards_count:1,publications_count:1}});t.held.set('/api/v3/feeds/feed/completion',true);});
  await r.page.locator('[data-feed="feed"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();await expect(r.page.locator('#feed-action-confirm')).toBeEnabled();await r.page.locator('#feed-action-confirm').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/feeds/feed/completion'));
  await r.page.evaluate(()=>{const t=readerTransport,path='/api/v3/feeds/feed/completion';t.pending.get(path)!(Promise.reject(new TypeError('Connection lost')));t.pending.delete(path);t.held.delete(path);});
  await expect(r.page.locator('#feed-action-dialog')).toContainText('Action outcome unknown');await expect(r.page.locator('#feed-action-confirm')).toBeDisabled();
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url==='/api/v3/feeds/feed/completion').length)).toBe(1);
  await r.page.locator('#feed-action-cancel').click();
  await r.page.locator('#status-toggle').click();await expect(r.page.locator('#status-panel')).toContainText('Action outcome unknown');await r.page.keyboard.press('Escape');
  await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/feeds/feed/completion/preview',{body:{token:'fresh',cards_count:2,publications_count:2}}));
  await r.page.locator('[data-feed="feed"]').click({button:'right'});await r.page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();await expect(r.page.locator('#feed-completion-count')).toContainText('Items: 2');await expect(r.page.locator('#feed-action-confirm')).toBeEnabled();
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url==='/api/v3/feeds/feed/completion/preview').length)).toBe(2);
 }finally{await r.close();}
});
