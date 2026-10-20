import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';
import type {FeedSettingsSnapshot} from '../../frontend/src/api/generated/types.gen';

async function scenario(t:Parameters<typeof productionReader>[0]){
 const r=await productionReader(t);
 const catalog=await addSources(r.api,[['https://a.test/feed','First feed','First folder'],['https://b.test/feed','Second feed','Second folder']]);
 const one=catalog.feeds.find(f=>f.title==='First feed')!,two=catalog.feeds.find(f=>f.title==='Second feed')!;
 await r.page.addInitScript(()=>{
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:(value:string)=>{
   const d=document.documentElement.dataset;
   if(d.copyFailure) return Promise.reject(new Error('Clipboard denied'));
   d.copied=value;
   return d.copyDelay?new Promise<void>(resolve=>window.addEventListener('release-copy',()=>resolve(),{once:true})):Promise.resolve();
  }}});
 });
 await r.page.goto(r.origin+'/#token='+r.token);
 await r.page.locator('#feeds-open').click();
 await r.page.locator('#feed-tree').getByRole('button',{name:one.title,exact:true}).click();
 await expect(r.page.locator('.feed-details')).toBeVisible();
 return {...r,one,two};
}

test('variant B saves title and folder, copies readonly link and preserves other settings', {timeout:30000},async t=>{
 const {page,api,one,two}=await scenario(t);
 await page.setViewportSize({width:1440,height:1050});
 const form=page.locator('.feed-details'),save=form.getByRole('button',{name:'Save',exact:true});
 await expect(page.locator('#feed-link-0')).toHaveAttribute('readonly','');
 await expect(form.locator('a')).toHaveCount(0);
 let patches=0;page.on('request',r=>{if(r.method()==='PATCH'&&r.url().includes('/feeds/'))patches++;});
 await form.getByRole('button',{name:'Copy link',exact:true}).click();
 await expect(form.getByRole('status')).toHaveText('Copied');
 expect(await page.evaluate(()=>document.documentElement.dataset.copied)).toBe(one.original_urls[0]);
 expect(patches).toBe(0);await expect(save).toBeDisabled();
 await page.evaluate(()=>{document.documentElement.dataset.copyFailure='yes';});
 await form.getByRole('button',{name:'Copy link',exact:true}).click();
 await expect(form.getByRole('status')).toContainText('Could not copy');
 await page.locator('#feed-title').fill('  Updated feed  ');
 await page.locator('#feed-folder').selectOption(two.folder_id);
 await save.click();
 await expect(page.locator('#settings-message')).toHaveText('Feed saved');
 expect(patches).toBe(1);
 const saved=await api(`settings/feed/${one.id}`);
 if(saved.scope!=='feed')throw Error('Expected feed settings');
 expect(saved.feed.title).toBe('Updated feed');expect(saved.feed.folder_id).toBe(two.folder_id);
 expect(saved.feed.id).toBe(one.id);expect(saved.feed.original_urls).toEqual(one.original_urls);
 await expect(page.locator('#feed-tree').getByRole('button',{name:'Updated feed',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(save).toBeDisabled();
 await page.locator('#feed-title').fill('Reset me');await form.getByRole('button',{name:'Reset',exact:true}).click();
 await expect(page.locator('#feed-title')).toHaveValue('Updated feed');
 await page.locator('#feed-title').fill('Cancel me');await form.getByRole('button',{name:'Cancel',exact:true}).click();
 await expect(page.locator('#feeds-dialog')).toBeHidden();await page.locator('#feeds-open').click();
 await expect(page.locator('#feed-title')).toHaveValue('Updated feed');
 await page.locator('#feed-title').fill('   ');await page.locator('#feed-title').blur();
 await form.getByRole('button',{name:'Reset',exact:true}).click();
 await page.locator('#feed-folder').selectOption(one.folder_id);await save.click();
 await expect(page.locator('#settings-message')).toHaveText('Feed saved');
 await expect(save).toBeDisabled();
 await page.getByRole('button',{name:'Connection and Options',exact:true}).click();
 await expect(page.locator('#feed-importance')).toBeVisible();await expect(page.locator('#feed-folder')).toBeHidden();
 await page.locator('.settings-back:visible').click();
 await page.screenshot({path:artifactPath('feed-details-B-desktop.png'),fullPage:true});
 await page.setViewportSize({width:820,height:900});
 expect(await page.locator('#feeds-dialog').evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true);
 await page.screenshot({path:artifactPath('feed-details-B-compact.png'),fullPage:true});
 await page.reload();await page.locator('#feeds-open').click();
 await page.locator('#feed-tree').getByRole('button',{name:'Updated feed',exact:true}).click();
 await expect(page.locator('#feed-title')).toHaveValue('Updated feed');
});

test('late feed save preserves selection and new drafts; duplicate submit stays blocked', {timeout:30000},async t=>{
 const {page,api,one,two}=await scenario(t);
 let release=()=>{},sent=0;const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 await page.route('**/api/v3/feeds/'+one.id,async route=>{if(route.request().method()!=='PATCH')return route.continue();sent++;const reply=await route.fetch();await held;await route.fulfill({response:reply});});
 await page.locator('#feed-title').fill('Saved first');await page.locator('.feed-details').getByRole('button',{name:'Save',exact:true}).click();
 await expect.poll(()=>sent).toBe(1);
 await expect(page.locator('.feed-details button[type=submit]')).toBeDisabled();
 await page.locator('#feed-title').fill('Newer first draft');
 await page.locator('#feed-tree').getByRole('button',{name:two.title,exact:true}).click();
 await page.locator('#feed-title').fill('Second draft');
 release();
 await expect(page.locator('#feed-tree').getByRole('button',{name:'Saved first',exact:true})).toBeVisible();
 await expect(page.locator('#feed-title')).toHaveValue('Second draft');
 await expect(page.locator('#settings-message')).not.toHaveText('Feed saved');
 await page.locator('#feed-tree').getByRole('button',{name:'Saved first',exact:true}).click();
 await expect(page.locator('#feed-title')).toHaveValue('Newer first draft');
 const result=await api(`settings/feed/${one.id}`);if(result.scope!=='feed')throw Error('Expected feed');expect(result.feed.title).toBe('Saved first');
 expect(sent).toBe(1);
});

test('copy and metadata responses belong to their opening; activity never resets a draft', {timeout:30000},async t=>{
 const {page,api,one,two}=await scenario(t);
 await page.evaluate(()=>{document.documentElement.dataset.copyDelay='yes';});
 await page.getByRole('button',{name:'Copy link',exact:true}).click();
 await page.locator('#feeds-close').click();await page.locator('#feeds-open').click();
 await page.evaluate(()=>window.dispatchEvent(new Event('release-copy')));
 await expect(page.locator('.feed-copy-status')).toBeEmpty();
 await page.locator('#feed-title').fill('Keep while activity changes');
 let release=()=>{},seen=false;const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 await page.route('**/api/v3/settings/feed/'+one.id,async route=>{
  const reply=await route.fetch(),body=await reply.json() as FeedSettingsSnapshot;seen=true;
  await held;try{await route.fulfill({json:{...body,activity:{created:null,modified:null,updated:null,marked_read:null,unread:99,description:'Late description'}}});}catch(error){if(!(error instanceof Error)||!error.message.includes('Route is already handled'))throw error;}
 });
 // Idle status no longer reloads an unchanged form. A real source update
 // refreshes activity through the existing settings owner.
 await api('collect','POST',{scope:'feed',feed_id:one.id});
 await expect.poll(()=>seen,{timeout:10000}).toBe(true);
 await page.locator('#feed-tree').getByRole('button',{name:two.title,exact:true}).click();
 await page.locator('#feed-title').fill('Second keeps its draft');
 release();await page.unrouteAll({behavior:'wait'});
 await expect(page.locator('#feed-title')).toHaveValue('Second keeps its draft');
 await expect(page.locator('.feed-activity')).not.toContainText('99');
 await page.locator('#feed-tree').getByRole('button',{name:one.title,exact:true}).click();
 await expect(page.locator('#feed-title')).toHaveValue('Keep while activity changes');
});

test('additional links copy exact bytes; missing and failed activity never becomes zero', {timeout:30000},async t=>{
 const {page,api,one}=await scenario(t);
 let mode:'links'|'empty'|'failed'='links';
 const url='https://source.test/feed?value=a%2Fb&x=1';
 await page.route('**/api/v3/settings/feed/'+one.id,async route=>{
  if(mode==='failed')return route.abort();
  const reply=await route.fetch(),body=await reply.json() as FeedSettingsSnapshot;
  await route.fulfill({json:{...body,feed:{...body.feed,original_urls:mode==='links'?[url,'https://second.test/feed']:[]},activity:mode==='links'?{created:null,modified:null,updated:null,marked_read:null,unread:0,description:'<script>do not execute</script>\nSource description'}:null}});
 });
 await page.locator('#feeds-close').click();await page.locator('#feeds-open').click();
 await expect(page.locator('#feed-link-1')).toHaveValue('https://second.test/feed');
 await page.getByRole('button',{name:'Copy link',exact:true}).click();
 await expect(page.locator('.feed-copy-status')).toHaveText('Copied');
 expect(await page.evaluate(()=>document.documentElement.dataset.copied)).toBe(url);
 await page.getByRole('button',{name:'Copy link 2',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>document.documentElement.dataset.copied)).toBe('https://second.test/feed');
 await expect(page.locator('.feed-activity .feed-unread strong')).toHaveText('0');
 await expect(page.locator('.feed-description')).toContainText('<script>do not execute</script>');
 await expect(page.locator('.feed-description script')).toHaveCount(0);
 mode='empty';await page.locator('#feeds-close').click();await page.locator('#feeds-open').click();
 await expect(page.locator('#feed-link-0')).toHaveCount(0);await expect(page.locator('.feed-details button[title="Copy link"]')).toHaveCount(0);
 await expect(page.locator('.feed-unread strong')).toHaveText('Unavailable');
 await page.locator('#feed-title').fill('Keep after read failure');
 mode='failed';
 await api('collect','POST',{scope:'feed',feed_id:one.id});
 await expect(page.locator('.feed-activity').getByRole('button',{name:'Retry',exact:true})).toBeVisible({timeout:10000});
 await expect(page.locator('.feed-unread strong')).toHaveText('Unavailable');
 await expect(page.locator('#feed-title')).toHaveValue('Keep after read failure');
});

test('initial settings failure keeps catalogue fields available and can be retried', {timeout:30000},async t=>{
 const {page,one}=await scenario(t);
 let fail=true;
 await page.route('**/api/v3/settings/feed/'+one.id,route=>fail?route.abort():route.continue());
 await page.reload();
 await page.locator('#feeds-open').click();await page.locator('#feed-tree').getByRole('button',{name:one.title,exact:true}).click();
 await expect(page.locator('#feed-title')).toHaveValue(one.title);
 await expect(page.locator('.feed-unread strong')).toHaveText('Unavailable');
 await page.locator('#feed-title').fill('Survives initial failure');
 await expect(page.locator('.feed-activity').getByRole('button',{name:'Retry',exact:true})).toBeVisible();
 fail=false;await page.locator('.feed-activity').getByRole('button',{name:'Retry',exact:true}).click();
 await expect(page.getByRole('button',{name:'Connection and Options',exact:true})).toBeVisible();
 await expect(page.locator('#feed-title')).toHaveValue('Survives initial failure');
 await expect(page.locator('.feed-unread strong')).not.toHaveText('Unavailable');
});

test('late first settings snapshot preserves the mounted title field and focus', {timeout:30000},async t=>{
 const {page,one}=await scenario(t);
 let release=()=>{};const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 await page.route('**/api/v3/settings/feed/'+one.id,async route=>{const reply=await route.fetch();await held;await route.fulfill({response:reply});});
 await page.reload();
 await page.locator('#feeds-open').click();await page.locator('#feed-tree').getByRole('button',{name:one.title,exact:true}).click();
 await page.locator('#feed-title').fill('Typing before initial response');
 await expect(page.locator('#feed-title')).toBeFocused();
 release();await expect(page.getByRole('button',{name:'Connection and Options',exact:true})).toBeVisible();
 await expect(page.locator('#feed-title')).toBeFocused();
 await expect(page.locator('#feed-title')).toHaveValue('Typing before initial response');
 await page.unrouteAll({behavior:'wait'});
});
