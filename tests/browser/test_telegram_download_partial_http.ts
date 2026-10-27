import {test} from 'node:test';
import assert from 'node:assert/strict';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('Broken Telegram stream saves text, a separate reason and an explicit retry through HTTP',async t=>{
 const r=await productionReader(t);
 await r.api('settings/oko/global','PATCH',{theses:false,prompt_filters:'',telegram_http_rps:10});
 await r.openReader();
 await r.api('telegram/connect','POST',{source_locator:'@brokenstream',folder:'Links',initial_history:{mode:'count',count:'1'}});
 await r.page.goto(r.origin+'/#token='+r.token);
 await expect(r.page.locator('[data-card]')).toHaveCount(1);
 await r.page.locator('[data-card]').click();
 const error=r.page.locator('#completeness');
 await expect(r.page.locator('#text')).toContainText('Текст сохранён до обрыва');
 await expect(error).toContainText('соединение оборвано');
 const card=((await r.api('cards')).items)[0]!;
 assert.equal(card.source_url,'https://t.me/brokenstream/11');
 await expect(r.page.getByRole('button',{name:'Retry Download',exact:true})).toBeVisible();
 await r.page.screenshot({path:artifactPath('telegram-download-partial-desktop.png')});
 await r.page.setViewportSize({width:390,height:844});
 await error.scrollIntoViewIfNeeded();
 await r.page.screenshot({path:artifactPath('telegram-download-partial-mobile.png')});
 assert.equal(await r.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await r.page.getByRole('button',{name:'Retry Download',exact:true}).click();
 await expect(error).toContainText('соединение оборвано');
 await expect(r.page.getByRole('button',{name:'Retry Download',exact:true})).toBeVisible();
});
