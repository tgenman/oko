import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('text stays visible through media waiting, error and manual retry', {timeout:25000},async t=>{
 const r=await productionReader(t,{publicationPipeline:true});
 await r.api('settings/oko/global','PATCH',{theses:false,prompt_filters:'',telegram_http_rps:10});
 await r.setMediaState('held');await r.openReader();
 await r.api('telegram/connect','POST',{source_locator:'@mediastate',folder:'Inbox',initial_history:{mode:'count',count:'1'}});
 await expect(r.page.locator('[data-card]')).toHaveCount(1);await r.page.locator('[data-card]').click();
 await expect(r.page.locator('#text')).toContainText('Text before media');await expect(r.page.locator('#completeness')).toContainText('loading attachments');
 await expect(r.page.locator('#images img')).toHaveCount(0);
 await r.setMediaState('error');await expect(r.page.locator('#completeness')).toContainText('Не удалось загрузить вложение');
 await expect(r.page.getByRole('button',{name:'Retry Download',exact:true})).toBeVisible();
 await r.page.screenshot({path:artifactPath('independent-media-error.png')});
 await r.setMediaState('ready');await r.page.getByRole('button',{name:'Retry Download',exact:true}).click();
 await expect(r.page.locator('#images img')).toHaveAttribute('src',/^blob:/);await expect(r.page.locator('#completeness')).toContainText('Fully saved');
 await expect(r.page.locator('#text')).toContainText('Text before media');
 await r.page.screenshot({path:artifactPath('independent-media-ready.png')});
});
