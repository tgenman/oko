import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('whole Telegram post uses link card C separately from audio and large video',{timeout:45000},async t=>{
 const {page,api,openReader}=await productionReader(t);
 await api('v3/settings/oko/global','PATCH',{theses:false,prompt_filters:'',telegram_http_rps:10});
 await openReader();
 await api('v3/telegram/connect','POST',{source_locator:'@postunavailable',folder:'News'});
 const post=page.locator('[data-card]').filter({hasText:'Пост доступен только в Telegram'});
 await expect(post.locator('[aria-label="Публикация недоступна"]')).toBeVisible({timeout:15000});
 await post.click();
 await expect(page.locator('#title')).toHaveText('Пост доступен только в Telegram');
 await expect(page.locator('#source-unavailable')).toContainText('Публикация недоступна для веб-просмотра');
 await expect(page.locator('#source-unavailable a')).toHaveAttribute('href','https://t.me/postunavailable/13');
 await expect(page.locator('#source-unavailable a')).toHaveText('Open original ↗');
 await page.locator('#source-unavailable a').focus();
 await expect(page.locator('#source-unavailable a')).toBeFocused();
 await expect(page.locator('#retry')).toBeVisible();
 await expect(page.locator('#text')).toHaveText('');
 await expect(page.locator('#theses-panel')).toBeHidden();
 await expect(page.locator('#filter-panel')).toBeHidden();
 await page.screenshot({path:artifactPath('telegram-whole-post-C.png')});
 await page.locator('[data-card]').filter({hasText:'External audio post'}).click();
 await expect(page.locator('#source-unavailable')).toHaveCount(0);
 await expect(page.locator('#text [aria-label="External media"]')).toContainText('Open original');
 await page.locator('[data-card]').filter({hasText:'External video post'}).click();
 await expect(page.locator('#source-unavailable')).toHaveCount(0);
 await expect(page.locator('#text [aria-label="External media"]')).toContainText('File exceeds the available size limit.');
 await post.click();
 await expect(page.locator('#source-unavailable')).toBeVisible();
 await expect(page.locator('#text [aria-label="External media"]')).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});
 await page.locator('#source-unavailable').screenshot({path:artifactPath('telegram-whole-post-C-narrow.png')});
 await expect.poll(async()=>{const s=await api('v3/status/details');return s.problems.count;}).toBeGreaterThan(0);
});


import {reader,material} from './support';
