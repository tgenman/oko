import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader.ts';

test('real HTTP settings load three YouTube materials and keep viewed IDs on a second selection', {timeout:45000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  const errors: string[]=[]; page.on('pageerror',error=>errors.push(error.message));
  await api('settings/oko/global','PATCH',{theses:false});
  const plan=await api('import','POST',{opml:'<opml><body><outline text="Наука"><outline text="Видео" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa"/></outline></body></opml>'});
  const feed=plan.feeds[0];
  await page.goto(origin+'/#token='+token);
  await page.locator('#feeds-open').click();
  await page.locator(`[data-owner="feed/${feed.id}"]`).click();
    await feedOptions(page);
  await page.locator('#youtube-history-count').fill('3');
  await page.locator('#youtube-history-start').click();
  await expect.poll(async()=>(await api(`settings/feed/${feed.id}`)).feed!.initial_history?.loaded,{timeout:20000}).toBe(3);
  await expect(page.locator('#history-progress')).toContainText('Загружено 3 из 3');
  await expect(page.locator('#youtube-history-start')).toBeEnabled();
  await page.locator('#feeds-close').click();
  await page.locator(`button[data-feed="${feed.id}"]`).click();
  await expect(page.locator('#cards button')).toHaveCount(3);
  await expect(page.locator('#cards .card-date').first()).toHaveText('Дата неизвестна');
  const latest=(await api('cards')).find(card=>card.url.endsWith('v0000000060'));
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
    const history=(await api(`settings/feed/${feed.id}`)).feed!.initial_history;
    return [history!.count, history!.state];
  }).toEqual([2, 'done']);
  expect((await api(`settings/feed/${feed.id}`)).feed!.initial_history!.viewed).toBe(1);
  expect((await api('cards')).length).toBe(2);
  expect(errors).toEqual([]);
});
