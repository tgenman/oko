import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import {artifactPath} from './artifacts';

test('power is shared across tabs, preserves reading and blocks external actions in open settings', {timeout:45000}, async t=>{
  const {page,context,api,origin,token}=await productionReader(t);
  await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
  await addSources(api,[['https://a.test/feed','Альфа','Наука']]);
  await page.goto(origin+'/#token='+token);
  await expect(page.locator('#cards button')).toHaveCount(1);
  await page.locator('#cards button').click();
  await expect(page.locator('#title')).not.toHaveText('');
  const title=await page.locator('#title').textContent();
  const second=await context.newPage();
  await second.goto(origin+'/#token='+token);
  await second.locator('#settings-open').click();
  await second.locator('[data-settings-target="models"]').click();
  await second.locator('[data-settings-target="codex"]').click();
  await page.locator('#stop').click();
  await expect(page.locator('#stop')).toHaveText('Start');
  await expect(second.locator('#stop')).toHaveText('Start');
  await expect(page.locator('#title')).toHaveText(title!);
  await expect(page.locator('#collect')).toBeDisabled();
  await expect(page.locator('#add-open')).toBeDisabled();
  await expect(second.locator('#codex-models-refresh')).toBeDisabled();
  await expect(second.locator('#codex-check')).toBeDisabled();
  await expect(second.locator('#codex-save')).toBeDisabled();
  await page.screenshot({path:artifactPath('power-disabled.png'),fullPage:true});
  await page.locator('#viewed').click();
  await expect(page.locator('#cards button')).toHaveCount(0);
  await page.locator('#stop').click();
  await expect(page.locator('#stop')).toHaveText('Stop');
  await expect(second.locator('#stop')).toHaveText('Stop');
  await expect(page.locator('#collect')).toBeEnabled();
  await expect(page.locator('#add-open')).toBeEnabled();
});


test('disabled power blocks an already open add form and survives browser reload', {timeout:30000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  await page.goto(origin+'/#token='+token);
  await page.locator('#add-open').click();
  await page.locator('#rss-url').fill('https://a.test/feed');
  await expect(page.locator('#rss-check')).toBeEnabled();
  await api('power','PUT',{enabled:false});
  await expect(page.locator('#rss-check')).toBeDisabled();
  await expect(page.locator('#add-dialog')).toBeVisible();
  await page.reload();
  await expect(page.locator('#stop')).toHaveText('Start');
  await expect(page.locator('#add-open')).toBeDisabled();
  await page.locator('#stop').click();
  await expect(page.locator('#add-open')).toBeEnabled();
  await page.locator('#add-open').click();
  await expect(page.locator('#rss-url')).toHaveValue('');
});
