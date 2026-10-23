import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('whole Telegram post uses link card C separately from audio and large video',{timeout:30000},async t=>{
 const {page,api,openReader}=await productionReader(t);
 await api('v3/settings/oko/global','PATCH',{theses:false,prompt_filters:'',telegram_http_rps:10});
 await openReader();
 await api('v3/telegram/connect','POST',{source_locator:'@postunavailable',folder:'News'});
 const post=page.locator('[data-card]').filter({hasText:'Пост доступен только в Telegram'});
 await expect(post.locator('[aria-label="Пост недоступен для веб-просмотра"]')).toBeVisible();
 await post.click();
 await expect(page.locator('#title')).toHaveText('Публикация в Telegram');
 await expect(page.locator('#post-web-unavailable')).toContainText('TELEGRAM · ВЕБ-ПРОСМОТР НЕДОСТУПЕН');
 await expect(page.locator('#post-web-unavailable')).toContainText('Fixture · пост №13');
 await expect(page.locator('#post-web-unavailable a')).toHaveAttribute('href','https://t.me/postunavailable/13');
 await expect(page.locator('#post-web-unavailable a')).toHaveText('Открыть в Telegram ↗');
 await page.locator('#post-web-unavailable a').focus();
 await expect(page.locator('#post-web-unavailable a')).toBeFocused();
 await expect(page.locator('#retry')).toBeHidden();
 await expect(page.locator('#text')).toHaveText('');
 await expect(page.locator('#theses-panel')).toBeHidden();
 await expect(page.locator('#filter-panel')).toBeHidden();
 await page.screenshot({path:artifactPath('telegram-whole-post-C.png')});
 await page.locator('[data-card]').filter({hasText:'External audio post'}).click();
 await expect(page.locator('#post-web-unavailable')).toHaveCount(0);
 await expect(page.locator('#external-attachments')).toContainText('Слушать в Telegram');
 await page.locator('[data-card]').filter({hasText:'External video post'}).click();
 await expect(page.locator('#post-web-unavailable')).toHaveCount(0);
 await expect(page.locator('#external-attachments')).toContainText('Видео слишком большое для веб-просмотра');
 await post.click();
 await expect(page.locator('#post-web-unavailable')).toBeVisible();
 await expect(page.locator('#external-attachments')).toHaveText('');
 await page.setViewportSize({width:390,height:844});
 await page.locator('#post-web-unavailable').screenshot({path:artifactPath('telegram-whole-post-C-narrow.png')});
 await expect.poll(async()=>{const s=await api('v3/status');return s.problems.count;}).toBe(0);
});


import {reader,material} from './support';

test('late unavailable Telegram post cannot replace another opened material',async()=>{
 const r=await reader();
 try{
  const post=material('P',{text:'',url:'https://t.me/fixture/11',full:false,post_web_unavailable:true});
  await r.start([material('A'),post]);
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/materials/P',true));
  await r.page.getByRole('button',{name:/^P ·/}).click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/materials/P'));
  await r.page.getByRole('button',{name:/^A ·/}).click();
  await expect(r.page.locator('#text')).toHaveText('Текст A');
  await r.page.evaluate(body=>readerTransport.release('/api/v3/materials/P',body),post);
  await expect(r.page.locator('#post-web-unavailable')).toHaveCount(0);
  await expect(r.page.locator('#text')).toHaveText('Текст A');
 }finally{await r.close();}
});
