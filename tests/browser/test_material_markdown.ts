import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reader,material,expect} from './support';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';

test('publication Markdown renders semantic text and safe links without external images',async()=>{
 const r=await reader();try{
  const text='# Заголовок\n\n**Важно** и *выделение*. [Статья](https://example.net/a_(b)?x=1&y=2) и [Относительная](../story).\n\n[По ссылке][ref]\n\n[ref]: https://example.net/ref\n\n<https://example.net/auto> https://example.net/plain\n\n- Первый\n- Второй\n\n> Цитата\n\n`[Код](https://code.test)`\n\n<script>alert(1)</script>\n\n[Опасно](javascript:alert(1)) [Файл](file:///etc/passwd) [Почта](mailto:reader@example.net)\n\n![Подпись изображения](https://image.test/x.png)';
  await r.start([material('A',{text,url:'https://example.org/posts/A'})]);await r.page.locator('[data-card="A"]').click();
  const body=r.page.locator('#text');
  await expect(body.getByRole('link',{name:'Статья',exact:true})).toHaveAttribute('href','https://example.net/a_(b)?x=1&y=2');
  await expect(body.getByRole('link',{name:'Относительная',exact:true})).toHaveAttribute('href','https://example.org/story');
  await expect(body.getByRole('link',{name:'По ссылке',exact:true})).toHaveAttribute('href','https://example.net/ref');
  await expect(body.locator('strong')).toHaveText('Важно');await expect(body.locator('em')).toHaveText('выделение');
  await expect(body.locator('li')).toHaveCount(2);await expect(body.locator('blockquote')).toHaveText('Цитата');
  await expect(body.locator('code')).toHaveText('[Код](https://code.test)');
  await expect(body.locator('script,img,iframe')).toHaveCount(0);
  await expect(body.locator('a[href^="javascript:"],a[href^="file:"]')).toHaveCount(0);
  await expect(body.getByRole('link',{name:'Почта'})).toHaveAttribute('href','mailto:reader@example.net');
  await expect(body).toContainText('Подпись изображения');
  await expect(body.locator('a[href="https://example.net/auto"]')).toHaveCount(1);await expect(body.locator('a[href="https://example.net/plain"]')).toHaveCount(1);
  await expect(body.getByRole('link',{name:'Статья',exact:true})).toHaveAttribute('rel','noopener noreferrer');
  await expect(body.getByRole('link',{name:'Статья',exact:true})).toHaveAttribute('target','_blank');
  await r.page.screenshot({path:artifactPath('markdown-desktop.png')});
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});


test('Markdown publication links travel through the production HTTP reader unchanged',async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,originals:false,processor:'llamacpp'});
 await addSources(api,[['https://a.test/markdown-feed','Markdown','Тексты']]);
 await page.goto(origin+'/#token='+token);
 await expect(page.locator('#cards button')).toHaveCount(1);
 await page.locator('#cards button').click();
 const link=page.locator('#text').getByRole('link',{name:'YouTube Christa Pike 911 Calls',exact:true});
 await expect(link).toHaveAttribute('href','https://youtu.be/BPoce9VGatE');
 await expect(page.locator('#text').getByRole('link',{name:'Статья',exact:true})).toHaveAttribute('href','https://www.upstartsmedia.com/p/tolan-raises-30m-for-ai-companion-you-trust');
 const cards=(await api('cards')).items;const response=await api(`publication/${cards[0]!.id}`);
 assert.equal(response.availability,'available');if(response.availability!=='available')throw Error('Expected available content');
 assert.ok(response.text.includes('[YouTube\n\nChrista Pike 911 Calls](https://youtu.be/\n  BPoce9VGatE)'));
 assert.ok(response.text.endsWith('[Статья](https://www.upstartsmedia.com/p/tolan-raises-30m-for-ai-companion-you-trust)'));
 assert.equal(await page.locator('#text').locator('xpath=ancestor::details').count(),0);
});

test('filtered publication keeps a collapsed full original, media and restorable Markdown text',async()=>{
 const r=await reader();try{
  const item=material('A',{text:'[Статья](https://example.net/story)\n\nРеклама',images:['/api/v3/materials/A/images/x.png'],filter:{status:'done',hidden:false,reason:'Убрана реклама',removed:['Реклама']}});
  await r.start([item]);
  await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/materials/A/images/x.png',{base64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1sAAAAASUVORK5CYII=',mime:'image/png'}));
  await r.page.locator('[data-card="A"]').click();
  await expect(r.page.locator('#filtered-text').getByRole('link',{name:'Статья'})).toBeVisible();
  await expect(r.page.locator('#filtered-text')).not.toContainText('Реклама');
  await expect(r.page.locator('#original-panel')).not.toHaveAttribute('open');
  await expect(r.page.locator('#images img')).toBeVisible();
  await r.page.locator('#original-panel summary').click();await expect(r.page.locator('#text').getByRole('link',{name:'Статья'})).toBeVisible();
  await expect(r.page.locator('#text')).toContainText('Реклама');await expect(r.page.locator('#material img')).toHaveCount(1);
  await expect(r.page.locator('#filter-restore')).toHaveCount(0);
  const hidden={...item,filter:{...item.filter!,hidden:true,reason:'Скрыто фильтром'}};
  await r.page.evaluate(hidden=>readerTransport.replies.set('/fixture/material/A',{body:hidden}),hidden);
  await expect(r.page.locator('#filter-status')).toContainText('Скрыто фильтром');await expect(r.page.locator('#text').getByRole('link',{name:'Статья'})).toBeVisible();
  await expect(r.page.locator('#original-panel')).toHaveCount(0);
  await r.page.evaluate(item=>readerTransport.replies.set('/fixture/material/A',{body:item}),item);
  await expect(r.page.locator('#original-panel')).toHaveCount(1);
  await expect(r.page.locator('#original-panel')).not.toHaveAttribute('open');
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});
