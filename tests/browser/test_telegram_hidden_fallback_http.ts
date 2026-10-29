import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('supported Telegram text ignores the hidden fallback and remains fully saved',async t=>{
 const {page,api,openReader}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:'',telegram_http_rps:10});
 await openReader();
 await api('telegram/connect','POST',{source_locator:'@hiddenfallback',folder:'News'});
 await page.locator('[data-card]').click();
 await expect(page.locator('#text')).toContainText('Available text 🔵');
 await expect(page.locator('#text').getByRole('link',{name:'Source',exact:true})).toHaveAttribute('href','https://example.test/source');
 await expect(page.locator('#post-web-unavailable')).toHaveCount(0);
 await expect(page.locator('#external-attachments')).toBeEmpty();
 await expect(page.locator('#completeness')).toHaveText('Fully saved');
 await expect(page.locator('#retry')).toBeHidden();
 await expect.poll(async()=>(await api('status/details')).problems.count).toBe(0);
 await page.screenshot({path:artifactPath('telegram-hidden-fallback.png')});
});
