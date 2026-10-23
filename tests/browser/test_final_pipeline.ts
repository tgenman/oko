import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('reader offers count history and no restore or persisted versions',{timeout:30000},async t=>{
 const r=await productionReader(t,{publicationPipeline:true});
 await r.api('settings/oko/global','PATCH',{theses:false,prompt_filters:'',telegram_http_rps:10});
 await r.openReader();
 await r.page.locator('#add-open').click();
 await r.page.locator('#add-telegram-tab').click();
 await expect(r.page.locator('#telegram-history-count')).toHaveValue('20');
 await expect(r.page.locator('#telegram-history-date')).toHaveCount(0);
 await expect(r.page.getByText('From Date',{exact:true})).toHaveCount(0);
 await r.page.locator('#telegram-folder-new').fill('Inbox');
 await r.page.locator('#telegram-peer').fill('@switchfixture');
 await r.page.locator('#telegram-add').click();
 await expect(r.page.locator('[data-card]')).toHaveCount(1);
 await r.page.locator('#feeds-close').click();
 await r.page.locator('[data-card]').click();
 await expect(r.page.locator('#text')).toContainText('Web original');
 await expect(r.page.locator('#filter-restore')).toHaveCount(0);
 await expect(r.page.locator('#versions')).toHaveCount(0);
 await r.page.screenshot({path:artifactPath('latest-publication-reader.png')});
});
