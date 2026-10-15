import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader.ts';
import assert from 'node:assert/strict';

test('production HTTP reader keeps folder/feed membership, previews, grouping and viewed actions', {timeout:45000}, async t => {
 const {page, api, origin, token} = await productionReader(t);
  assert.equal((await fetch(origin+'/api/cards')).status,403);
  await api('settings/oko/global','PATCH',{events:false,theses:false,processor:'llamacpp'});
  const plan=await api('import','POST',{opml:'<opml><body><outline text="Наука"><outline text="Альфа" xmlUrl="https://a.test/feed"/><outline text="Бета" xmlUrl="https://b.test/feed"/><outline text="Пустой" xmlUrl="https://empty.test/feed"/></outline></body></opml>'});
  const errors: string[]=[]; page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin+'/#token='+token);
  await expect.poll(async()=>{const cards=await api('cards'); return cards.length===2&&cards.every(c=>c.complete&&!c.loading);},{timeout:15000}).toBe(true);
  await expect(page.locator('#cards button')).toHaveCount(2);
  await expect(page.locator('#cards .card-snippet').first()).toHaveText('Открылась библиотека на 24 места.');
  await expect(page.locator('#cards img')).toHaveCount(2);
  await expect.poll(()=>page.locator('#cards img').evaluateAll(images=>images.every(img=>(img as HTMLImageElement).complete&&(img as HTMLImageElement).naturalWidth>0))).toBe(true);
  const alpha=plan.feeds.find(feed=>feed.title==='Альфа'), beta=plan.feeds.find(feed=>feed.title==='Бета'), empty=plan.feeds.find(feed=>feed.title==='Пустой');
  const navigation=await api('navigation'), folder=navigation.folders.find(folder=>folder.title==='Наука');
  await page.locator(`[data-folder="${folder!.id}"]`).click();
  await expect(page.locator('#count-label')).toHaveText('2 карточки');
  for(const feed of [alpha,beta]){
   await page.locator(`[data-feed="${feed!.id}"]`).click();
   await expect(page.locator('#cards button')).toHaveCount(1);
   await expect(page.locator('#cards .card-source')).toHaveText(feed!.title);
  }
  await api('processors/llamacpp','PUT',{endpoint:'http://localhost:9931',model:'local:latest',local_execution:true});
  await api('settings/oko/global','PATCH',{events:true});
  await expect.poll(async()=>{const cards=await api('cards');return cards.length===1&&!!cards[0].event;},{timeout:15000}).toBe(true);
  for(const feed of [alpha,beta]){
   await page.locator(`[data-feed="${feed!.id}"]`).click();
   await expect(page.locator('#cards button')).toHaveCount(1);
   await expect(page.locator('#cards .card-kind')).toHaveText('Событие');
   await expect(page.locator('#cards .card-source')).toContainText('Альфа');
   await expect(page.locator('#cards .card-source')).toContainText('Бета');
  }
  await page.locator(`[data-feed="${empty!.id}"]`).click();
  await expect(page.locator('#cards button')).toHaveCount(0);
  await expect(page.locator('#list-status')).toHaveText('Нет материалов');
  await page.locator(`[data-folder="${folder!.id}"]`).click();
  await expect(page.locator('#count-label')).toHaveText('1 карточка');
  await expect(page.locator(`[data-folder="${folder!.id}"] .tree-count`)).toHaveText('2');
  await page.locator('#cards button').click();
  await expect(page.locator('#title')).toHaveText('Открылась библиотека');
  await page.locator('#viewed').click();
  await expect(page.locator('#cards button')).toHaveCount(0);
  assert.deepEqual(await api('cards'),[]); assert.equal((await api('navigation')).count,0);
  assert.deepEqual(errors,[]);
});
