import {addSources} from './connections';
import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader.ts';

test('real HTTP settings load three YouTube materials and can rediscover a viewed publication on a second selection', {timeout:45000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  const errors: string[]=[]; page.on('pageerror',error=>errors.push(error.message));
  await api('settings/oko/global','PATCH',{theses:false});
  const plan=await addSources(api, [["https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa", "Видео", "Наука"]]);
  const feed=plan.feeds[0];
  async function history(){const snapshot=await api(`settings/feed/${feed.id}`);if(snapshot.scope!=='feed')throw new Error('Expected feed settings');return snapshot.feed.history;}
  await page.goto(origin+'/#token='+token);
  await page.locator('#feeds-open').click();
  await page.locator(`[data-owner="feed/${feed.id}"]`).click();
    await feedOptions(page);
  await page.locator('#youtube-history-count').fill('3');
  await page.locator('#youtube-history-start').click();
  await expect.poll(async()=>{const value=await history();return value.state==='absent'?0:value.progress.loaded;},{timeout:20000}).toBe(3);
  await expect(page.locator('#history-progress')).toContainText('Loaded 3 of 3');
  await expect(page.locator('#youtube-history-start')).toBeEnabled();
  await page.locator('#feeds-close').click();
  await page.locator(`button[data-feed="${feed.id}"]`).click();
  await expect(page.locator('#cards button')).toHaveCount(3);
  await expect(page.locator('#cards .date-group').first()).toHaveText('Unknown date');
  await expect(page.locator('#cards .card-date')).toHaveCount(0);
  const latest=(await api('cards')).find(card=>card.source_url.endsWith('v0000000060'));
  expect(latest).toBeDefined();
  await page.locator(`button[data-card="${latest!.id}"]`).click();
  await page.locator('#viewed').click();
  await expect(page.locator('#cards button')).toHaveCount(2);
  await page.locator('#feeds-open').click();
  await page.locator(`[data-owner="feed/${feed.id}"]`).click();
    await feedOptions(page);
  await page.locator('#youtube-history-count').fill('2');
  await page.locator('#youtube-history-start').click();
  // The previous three-item selection is already done while the new POST is in flight.
  await expect.poll(async()=>{
    const value=await history();
    return [value.state==='absent'?null:value.selection.mode==='count'?value.selection.count:null,value.state];
  }).toEqual(['2', 'complete']);
  const finalHistory=await history();expect(finalHistory.state!=='absent'&&finalHistory.progress.viewed).toBe(0);
  expect((await api('cards')).length).toBe(3); // Retain the third card and rediscover the viewed one in the new window.
  expect(errors).toEqual([]);
});
