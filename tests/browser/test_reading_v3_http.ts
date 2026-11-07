import {openAdmin,openReading} from './admin-navigation';
import {addSources} from './connections';
import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';
import {validate} from './contracts';

test('a typed action error is presented independently of the server wording', {timeout:20000}, async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 await addSources(api, [['https://a.test/feed','Альфа','Наука']]);
 await page.goto(origin+'/#token='+token);
 await page.locator('#cards button').first().click();
 await expect(page.locator('#text')).toContainText('Открылась библиотека');
 let count=0;
 await page.route('**/api/v3/materials/*/viewed',route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({code:'runtime_stopped',message:++count===1?'Старая формулировка':'Изменённая формулировка',details:null,diagnostic_id:null,command_id:null})}));
 for(let i=0;i<2;i++){
  await openReading(page);await page.locator('#viewed').click();
  await expect(page.locator('#message')).toHaveText('Oko stopped');
 }
 expect(count).toBe(2);
});

test('hide read hides folders without unviewed feeds even when selected', {timeout:25000}, async t=>{
 const {page,api,origin,token}=await productionReader(t);
 const catalog=await addSources(api,[["https://a.test/feed","Альфа","Новости"],["https://b.test/feed","Бета","Наука"]]);
 const folder=catalog.folders.find(f=>f.title==='Новости')!,feed=catalog.feeds.find(f=>f.title==='Альфа')!;
 const row=page.locator('[data-folder="'+folder.id+'"]'),toggle=page.locator('#read-feeds-toggle');
 await page.goto(origin+'/#token='+token);
 await row.click();
 await expect(page.locator('#cards button')).toHaveCount(1);
 await openReading(page);await page.locator('#cards button').click();
 await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await openReading(page);await page.locator('#viewed').click();
 await expect(row).toHaveCount(0);
 await expect(page.locator('#list-title')).toHaveText('Новости');
 await expect(page.locator('[data-folder]')).toHaveCount(1);
 await toggle.click();
 await expect(row).toBeVisible();
 await expect(row).toHaveAttribute('aria-pressed','true');
 await toggle.click();
 await expect(row).toHaveCount(0);
 await toggle.click();
 await page.locator('[data-feed="'+feed.id+'"]').click();
 await toggle.click();
 await expect(row).toHaveCount(0);
 await expect(page.locator('#list-title')).toHaveText('Альфа');
 await page.screenshot({path:artifactPath('hide-read-folders.png')});
 await toggle.click();
 await expect(row).toBeVisible();
 await expect(page.locator('[data-feed="'+feed.id+'"]')).toHaveAttribute('aria-pressed','true');
 expect((await api('v3/subscriptions')).feeds).toHaveLength(2);
});

test('show read reveals viewed publications and hide read hides them again', {timeout:25000}, async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 await addSources(api, [['https://a.test/feed','Альфа','Новости']]);
 await page.goto(origin+'/#token='+token);
 const cards=page.locator('#cards button'), toggle=page.locator('#read-feeds-toggle');
 await expect(cards).toHaveCount(1);
 await cards.first().click();
 await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await page.locator('#viewed').click();
 await expect(cards).toHaveCount(0);
 await toggle.click();
 await expect(cards).toHaveCount(1);
 await expect(cards.first()).toContainText('Read');
 await page.screenshot({path:artifactPath('viewed-card.png')});
 await cards.first().click({button:'right'});
 await page.getByRole('menuitem',{name:'Mark as Unread'}).click();
 await expect(cards.first()).not.toContainText('Read');
 await toggle.click();
 await expect(cards).toHaveCount(1);
});

test('deleting a feed closes its open publication until the feed is restored', {timeout:25000}, async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api, [['https://a.test/feed','Альфа','Новости']]);
 const feed=catalog.feeds.find(value=>value.title==='Альфа')!;
 await page.goto(origin+'/#token='+token);
 await page.locator('#cards button').first().click();
 await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await api(`feeds/${feed.id}`,'DELETE');
 await expect(page.locator('#cards button')).toHaveCount(0);
 await expect(page.locator('#material')).toBeHidden();
});

test('late opening cannot show a publication after its feed is deleted', {timeout:25000}, async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 const catalog=await addSources(api, [['https://a.test/feed','Альфа','Новости']]);
 const feed=catalog.feeds.find(value=>value.title==='Альфа')!;
 await page.goto(origin+'/#token='+token);
 let release!:()=>void,started!:()=>void,finished!:()=>void;
 const waiting=new Promise<void>(resolve=>release=resolve),received=new Promise<void>(resolve=>started=resolve),delivered=new Promise<void>(resolve=>finished=resolve);
 await page.route('**/api/v3/publication/*', async route=>{const response=await route.fetch();started();await waiting;await route.fulfill({response}).catch(()=>{});finished();});
 await page.locator('#cards button').first().click();
 await received;
 await api(`feeds/${feed.id}`,'DELETE');
 await expect(page.locator('#cards button')).toHaveCount(0);
 release();
 await delivered;
 await expect(page.locator('#material')).toBeHidden();
});

test('v3 reading uses real media and reconciles a lost viewed response without replay', {timeout:25000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
  await addSources(api, [["https://a.test/feed", "Альфа", "Наука"], ["https://b.test/feed", "Бета", "Наука"]]);
  const invalid:string[]=[];
  page.on('response',async response=>{
    if(!response.ok())return;
    const path=new URL(response.url()).pathname;
    const schema=path==='/api/v3/cards'?'cards_v3':path==='/api/v3/reading-composition'?'navigation_v3':/^\/api\/v3\/materials\/[^/]+$/.test(path)?'material_v3':null;
    if(schema)try{validate(schema,await response.json());}catch(error){invalid.push(String(error));}
  });
  await page.goto(origin+'/#token='+token);
  await expect(page.locator('#cards button')).toHaveCount(2);
  await page.locator('#cards button').first().click();
  await expect(page.locator('#text')).toContainText('Открылась библиотека');
  await expect(page.locator('#images img')).toHaveCount(1);
  await expect(page.locator('#cards .card-kind').filter({hasText:'Loading'})).toHaveCount(0);
  await expect(page.locator('#cards img')).toHaveCount(2);
  const desktop=artifactPath('reading-v3-desktop.png');
  await page.screenshot({path:desktop,fullPage:true});
  await page.setViewportSize({width:390,height:844});
  const mobile=artifactPath('reading-v3-mobile.png');
  await page.locator('#material').screenshot({path:mobile});
  console.log('Reading screenshots: '+desktop+' '+mobile);
  await page.setViewportSize({width:1280,height:900});
  let sent=0,command='';
  await page.route('**/api/v3/materials/*/viewed',async route=>{
    sent++;
    command=route.request().headers()['x-oko-action'];
    await route.fetch();
    await route.abort();
  });
  await openReading(page);await page.locator('#viewed').click();
  await expect(page.locator('#cards button')).toHaveCount(1);
  await openReading(page);await page.locator('#cards button').click();
  await expect(page.locator('#text')).toContainText('Открылась библиотека');
  await expect.poll(async()=>{
    if(!command)return '';
    const response=await page.request.get(origin+'/api/v3/commands/'+command,{headers:{Authorization:'Bearer '+token}});
    return (await response.json()).outcome;
  }).toBe('succeeded');
  await openAdmin(page);await page.locator('#status-toggle').click();
  await expect(page.locator('#status-panel')).not.toContainText('Action outcome unknown');
  expect(sent).toBe(1);
  expect(invalid).toEqual([]);
});

test('unconfirmed commands for two materials remain separate and do not replay', {timeout:20000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
  await addSources(api, [["https://a.test/feed", "Альфа", "Наука"], ["https://b.test/feed", "Бета", "Наука"]]);
  let sent=0;
  await page.route('**/api/v3/command-observations',route=>route.abort());
  await page.route('**/api/v3/materials/*/viewed',route=>{sent++;return route.abort();});
  await page.goto(origin+'/#token='+token);
  await expect(page.locator('#cards button')).toHaveCount(2);
  for(let i=0;i<2;i++) {
    await page.locator('#cards button').nth(i).click();
    await expect(page.locator('#text')).toContainText('Открылась библиотека');
    await openReading(page);await page.locator('#viewed').click();
    await expect(page.locator('#message')).not.toBeEmpty();
  }
  await openAdmin(page);await page.locator('#status-toggle').click();
  await expect(page.locator('.status-problem').filter({hasText:'Action outcome unknown'})).toHaveCount(2);
  expect(sent).toBe(2);
});

test('real HTTP completed feed stays selected, then hides and can be shown without changing reading', {timeout:25000}, async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await addSources(api,[["https://a.test/feed","Альфа","Новости"],["https://b.test/feed","Бета","Новости"]]);
 const catalog=await api('v3/subscriptions');
 const a=catalog.feeds.find(f=>f.title==='Альфа')!,b=catalog.feeds.find(f=>f.title==='Бета')!;
 await page.goto(origin+'/#token='+token);
 await expect(page.locator('#cards button')).toHaveCount(2);
 await page.locator('[data-feed="'+a.id+'"]').click();
 await expect(page.locator('#cards button')).toHaveCount(1);
 await openReading(page);await page.locator('#cards button').click();
 await expect(page.locator('#text')).toContainText('Открылась библиотека');
 await openReading(page);await page.locator('#viewed').click();
 await expect(page.locator('[data-feed="'+a.id+'"] .feed-completed-label')).toHaveText('All Read');
 await expect(page.locator('[data-feed="'+a.id+'"]')).toHaveAttribute('aria-pressed','true');
 await page.locator('#all-folders').click();
 await expect(page.locator('[data-feed="'+a.id+'"]')).toHaveCount(0);
 await expect(page.locator('#cards button')).toHaveCount(1);
 await page.locator('#read-feeds-toggle').click();
 await expect(page.locator('[data-feed="'+a.id+'"]')).toBeVisible();
 await page.locator('[data-feed="'+b.id+'"]').click();
 await openReading(page);await page.locator('#cards button').click();
 await expect(page.locator('#text')).toContainText('Открылась библиотека');
 const text=await page.locator('#text').textContent();
 const screenshot=artifactPath('read-feeds-toggle-a.png');await page.screenshot({path:screenshot});console.log('Completed feeds screenshot: '+screenshot);
 await page.locator('#read-feeds-toggle').click();
 await expect(page.locator('[data-feed="'+a.id+'"]')).toHaveCount(0);
 await expect(page.locator('[data-feed="'+b.id+'"]')).toHaveAttribute('aria-pressed','true');
 await expect(page.locator('#text')).toHaveText(text!);
 expect((await api('v3/subscriptions')).feeds).toHaveLength(2);
});
