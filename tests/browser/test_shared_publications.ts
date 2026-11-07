import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';

test('duplicate publication reports a feed conflict and original feed can be restored',{timeout:30000},async t=>{
 const r=await productionReader(t,{publicationPipeline:true});
 await r.setYoutubeState('ready');
 await r.api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 await r.openReader();
 const first='https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa';
 const second='https://www.youtube.com/feeds/videos.xml?channel_id=UCbbbbbbbbbbbbbbbbbbbbbb';
 await addSources(r.api,[[first,'First','One']]);
 await expect(r.page.locator('[data-card]')).toHaveCount(1);
 const item=((await r.api('cards')).items)[0]!.id;
 const owner=(await r.api(`publication/${item}`)).feed_id;
 await addSources(r.api,[[second,'Second','Two']]);
 await expect.poll(async()=>JSON.stringify(await r.api('status/details'))).toContain('Конфликт публикации');
 expect(((await r.api('cards')).items).map(card=>card.id)).toEqual([item]);
 await r.api(`feeds/${owner}`,'DELETE');
 await expect(r.page.locator(`[data-feed="${owner}"]`)).toHaveCount(0);
 expect((await r.api('cards')).items).toHaveLength(0);
 const restored=await addSources(r.api,[[first,'First','One']]);
 expect(restored.feeds.find(feed=>feed.original_urls.includes(first))?.id).toBe(owner);
 await expect.poll(async()=>(await r.api('cards')).items.some(card=>card.id===item)).toBe(true);
 await r.page.screenshot({path:artifactPath('publication-feed-restored.png')});
});
