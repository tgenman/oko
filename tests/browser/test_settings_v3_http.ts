import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader.ts';
import {artifactPath} from './artifacts.ts';

test('lost v3 settings response is reconciled without replay and keeps a newer draft', {timeout:20000}, async t=>{
  const {page,origin,token}=await productionReader(t);
  let sent=0, online=false;
  let release:()=>void=()=>{};
  const held=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/v3/command-observations', async route=>{if(online)await route.continue();else await route.abort();});
  await page.route('**/api/v3/settings/oko/global',async route=>{
    if(route.request().method()!=='PATCH')return route.continue();
    sent++;
    await route.fetch();
    await held;
    await route.abort();
  });
  await page.goto(origin+'/#token='+token);
  await page.locator('#settings-open').click();
  await page.locator('[data-settings-target="prompts"]').click();
  await page.locator('[data-settings-target="field/prompt_theses"]:visible').click();
  await page.locator('#setting-prompt_theses').fill('Сохранённый текст');
  await page.locator('#setting-save-prompt_theses').click();
  await expect.poll(()=>sent).toBe(1);
  await page.locator('#setting-prompt_theses').fill('Более новый черновик');
  release();
  await expect(page.locator('#setting-save-prompt_theses')).toBeEnabled();
  await expect(page.locator('#setting-prompt_theses')).toHaveValue('Более новый черновик');
  await page.locator('#settings-close').click();
  await page.locator('#status-toggle').click();
  await expect(page.locator('#status-panel')).toContainText('Action outcome unknown');
  const screenshot=artifactPath('settings-v3-desktop.png');
  await page.screenshot({path:screenshot,fullPage:true});
  console.log('Settings desktop screenshot: '+screenshot);
  // Consume the changed server status while observations are still offline.
  // Recovery must also retry after a confirmed *unchanged* status frame.
  for(let i=0;i<2;i++)await page.waitForResponse(async response=>{
    return response.url().endsWith('/api/v3/status')&&response.status()===304;
  });
  online=true;
  await expect(page.locator('#status-panel')).not.toContainText('Action outcome unknown',{timeout:8000});
  await expect(page.locator('#status-toggle')).toHaveText('Status');
  expect(sent).toBe(1);
  const response=await page.request.get(origin+'/api/v3/settings/oko/global',{headers:{Authorization:'Bearer '+token}});
  expect((await response.json()).effective.prompt_theses.value).toBe('Сохранённый текст');
});

test('filter switch saves independently from the filtering prompt', {timeout:20000}, async t=>{
  const {page,origin,token}=await productionReader(t);
  const headers={Authorization:'Bearer '+token};
  const saved=await page.request.patch(origin+'/api/v3/settings/oko/global',{headers,data:{prompt_filters:'Keep useful text',filter_enabled:true}});
  expect(saved.status()).toBe(200);
  await page.goto(origin+'/#token='+token);
  await page.locator('#settings-open').click();
  await page.locator('[data-settings-target="filters"]').click();
  await page.locator('[data-settings-target="field/filter_enabled"]').click();
  await page.locator('#setting-filter_enabled').uncheck();
  await page.locator('#setting-save-filter_enabled').click();
  await expect.poll(async()=>{
    const response=await page.request.get(origin+'/api/v3/settings/oko/global',{headers});
    return (await response.json()).effective.filter_enabled.value;
  }).toBe(false);
  const screenshot=artifactPath('filter-switch-desktop.png');
  await page.screenshot({path:screenshot,fullPage:true});
  console.log('Filter switch screenshot: '+screenshot);
  const result=await page.request.get(origin+'/api/v3/settings/oko/global',{headers});
  expect((await result.json()).effective.prompt_filters.value).toBe('Keep useful text');
});
