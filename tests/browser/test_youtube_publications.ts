import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';

for(const initial of ['ready','error'] as const) test(`common YouTube publication: ${initial} captions and independent cover`,{timeout:30000},async t=>{
 const r=await productionReader(t,{publicationPipeline:true});
 await r.setYoutubeState(initial);
 await r.setMediaState('held');
 await r.api('processors/llamacpp','PUT',{endpoint:'http://localhost:9931',model:'local:latest',local_execution:true});
 await r.api('settings/oko/global','PATCH',{processor:'llamacpp',theses:true,prompt_filters:''});
 await r.openReader();
 await addSources(r.api,[['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa','Synthetic YouTube','Inbox']]);
 await expect(r.page.locator('[data-card]')).toHaveCount(1);
 await r.page.locator('[data-card]').click();
 const card=(await r.api('cards'))[0];
 expect(card.source_url).toBe('https://www.youtube.com/watch?v=abcdefghijk');
 if(initial==='error'){
  await expect(r.page.locator('#completeness')).toContainText('Synthetic caption error');
  expect((await r.api(`materials/${card.id}`)).theses.result).toBeNull();
  await r.page.screenshot({path:artifactPath('youtube-caption-error.png')});
  await r.setYoutubeState('missing');
  await r.page.getByRole('button',{name:'Retry Download',exact:true}).click();
  await expect.poll(async()=>{const m=await r.api(`materials/${card.id}`);return m.theses.result?.points.length;}).toBe(0);
  if(await r.page.getByRole('button',{name:'Show new version',exact:true}).isVisible())await r.page.getByRole('button',{name:'Show new version',exact:true}).click();
  await r.page.screenshot({path:artifactPath('youtube-caption-missing.png')});
  await r.setYoutubeState('ready');
  await r.page.getByRole('button',{name:'Retry Download',exact:true}).click();
  await expect(r.page.getByRole('button',{name:'Show new version',exact:true})).toBeVisible();
  await r.page.getByRole('button',{name:'Show new version',exact:true}).click();
 }
 await expect(r.page.locator('#text')).toContainText('Raw YouTube captions.');
 await expect.poll(async()=>Boolean((await r.api(`materials/${card.id}`)).theses.result?.points.length)).toBe(true);
 const material=await r.api(`materials/${card.id}`);
 expect('attachments' in material.content && material.content.attachments.some(a=>a.kind==='image')).toBe(false);
 await r.page.screenshot({path:artifactPath(`youtube-${initial}-captions-before-cover.png`)});
 await r.setMediaState('ready');
 await expect.poll(async()=>{const m=await r.api(`materials/${card.id}`);return 'attachments' in m.content && m.content.attachments.some(a=>a.kind==='image');}).toBe(true);
 await expect(r.page.locator('#images img')).toHaveAttribute('src',/^blob:/);
 await expect(r.page.locator('#text')).toContainText('Raw YouTube captions.');
 await expect(r.page.locator('#theses li')).toHaveCount(1);
 await r.page.screenshot({path:artifactPath(`youtube-${initial}-complete.png`)});
});
