import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';

test('one publication belongs to both feeds and survives deleting its first feed',{timeout:30000},async t=>{
 const r=await productionReader(t,{publicationPipeline:true});
 await r.setYoutubeState('ready');
 await r.api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 await r.openReader();
 await addSources(r.api,[['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa','First','One'],['https://www.youtube.com/feeds/videos.xml?channel_id=UCbbbbbbbbbbbbbbbbbbbbbb','Second','Two']]);
 const feeds=(await r.api('subscriptions')).feeds;
 await expect(r.page.locator('[data-card]')).toHaveCount(1);
 const item=((await r.api('cards')).items)[0]!.id;
 await expect.poll(async()=>(await r.api('reading-composition')).feeds.map(f=>f.count)).toEqual([1,1]);
 for(const feed of feeds){
  await r.page.locator(`[data-feed="${feed.id}"]`).click();
  await expect(r.page.locator('[data-card]')).toHaveCount(1);
 }
 await r.page.locator('[data-card]').click();
 await expect(r.page.locator('#images img')).toHaveAttribute('src',/^blob:/);
 const material=await r.api(`materials/${item}`);
 const survivor=feeds.find(f=>f.id!==material.feed_id)!;
 await r.api(`feeds/${material.feed_id}`,'DELETE');
 await expect(r.page.locator(`[data-feed="${material.feed_id}"]`)).toHaveCount(0);
 await r.page.locator(`[data-feed="${survivor.id}"]`).click();
 await expect(r.page.locator('[data-card]')).toHaveCount(1);
 await r.page.locator('[data-card]').click();
 await expect(r.page.locator('#images img')).toHaveAttribute('src',/^blob:/);
 expect(((await r.api('cards')).items).map(c=>c.id)).toEqual([item]);
 await r.page.screenshot({path:artifactPath('shared-publication-survives.png')});
 await r.page.getByRole('button',{name:'Mark as Read',exact:true}).click();
 await expect(r.page.locator('[data-card]')).toHaveCount(0);
 expect((await r.api('reading-composition')).count).toBe(0);
 await r.page.screenshot({path:artifactPath('shared-publication-viewed.png')});
});
