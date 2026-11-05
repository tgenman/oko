import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';

for(const kind of ['rss','telegram'] as const) test(`${kind} root stays readable and waits for its article before theses`,{timeout:45000},async t=>{
 const r=await productionReader(t,{publicationPipeline:true});
 await r.setArticleState('held');
 await r.setMediaState('held');
 await r.api('processors/llamacpp','PUT',{endpoint:'http://localhost:9931',model:'local:latest',local_execution:true});
 await r.api('settings/oko/global','PATCH',{processor:'llamacpp',theses:true,prompt_filters:'',telegram_http_rps:10,retry_initial_seconds:1,retry_max_seconds:1});
 await r.openReader();
 if(kind==='rss') await addSources(r.api,[['https://a.test/linked-feed','RSS pipeline','Inbox']]);
 else await r.api('telegram/connect','POST',{source_locator:'@linkedposts',folder:'Inbox',initial_history:{mode:'count',count:'1'}});
 await expect(r.page.locator('[data-card]')).toHaveCount(1);
 await r.page.locator('[data-card]').click();
 const own=kind==='rss'?'Own announcement.':'Own Telegram post.';
 await expect(r.page.locator('#text')).toContainText(own);
 await expect.poll(async()=>{const cards=(await r.api('cards')).items;return !!cards[0]&&(await r.api(`publication/${cards[0].id}`)).summarized_content.points===undefined;}).toBe(true);
 await expect(r.page.locator('#theses')).not.toContainText(own);
 await r.setArticleState('error');
 await expect(r.page.locator('#completeness')).toContainText('Не удалось загрузить статью');
 await expect(r.page.locator('#text')).toContainText(own);
 const pendingCard=(await r.api('cards')).items[0]!;
 expect((await r.api(`publication/${pendingCard.id}`)).summarized_content.points).toBeUndefined();
 await r.page.screenshot({path:artifactPath(`linked-${kind}-error.png`)});
 // Make the source available at the real retry, before the backend receives it.
 // An automatic retry before the click would otherwise stage a new version.
 await r.page.route('**/api/v3/materials/*/retry',async route=>{await r.setArticleState('ready');await route.continue();},{times:1});
 await r.page.getByRole('button',{name:'Retry Download',exact:true}).click();
 await r.page.getByRole('button',{name:'Show new version',exact:true}).click({timeout:15000});
 await expect(r.page.locator('#text')).toContainText('Article text arrived.');
 await expect(r.page.locator('#text [aria-label="Linked publication"]')).toBeVisible();
 await expect(r.page.locator('#theses')).toContainText('Article text arrived.');
 await expect(r.page.locator('#theses')).toContainText(own);
 await expect(r.page.locator('[data-card]')).toHaveCount(1);
 const cards=(await r.api('cards')).items;
 const material=await r.api(`publication/${cards[0].id}`);
 if(!material.summarized_content.points) throw new Error('Missing accepted theses');
 const refs=material.summarized_content.points.flatMap(point=>point.source_publication_ids);
 expect(material.linked_publications.some(article=>refs.includes(article.id)&&article.source_url.includes(kind==='rss'?'linked.example':'telegra.ph'))).toBe(true);
 await r.setMediaState('error');
 await expect(r.page.locator('#completeness')).toContainText('Не удалось загрузить вложение');
 await r.setMediaState('ready');
 await r.page.getByRole('button',{name:'Retry Download',exact:true}).click();
 await expect.poll(async()=>{const current=await r.api(`publication/${cards[0]!.id}`);return current.status!=='pending_collection'&&current.media.some(m=>m.kind==='image'&&m.status==='ready'&&m.url!==null);}).toBe(true);
 // The open tab may retain its earlier snapshot; selecting the card accepts current content.
 await r.page.locator('[data-card]').click();
 const image=r.page.locator('#text .linked-publication img');
 await expect(image).toHaveAttribute('src',/^blob:/);
 await r.page.screenshot({path:artifactPath(`linked-${kind}-ready.png`)});
});
