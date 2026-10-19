import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader.ts';
import {artifactPath} from './artifacts.ts';

test('Oko feed refresh interval saves explicitly, validates minutes and resets to 15', {timeout:20000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  const settings=async()=>{const result=await api('settings/oko/global');if(result.scope!=='oko')throw new Error('Expected global settings');return result;};
  await page.goto(origin+'/#token='+token);
  await page.locator('#settings-open').click();
  await page.getByRole('button',{name:'Feed Refresh Interval',exact:true}).click();
  const input=page.getByRole('spinbutton',{name:'Interval (minutes)',exact:true});
  await expect(input).toHaveValue('15');
  await input.fill('25');
  expect((await settings()).effective.feed_refresh_minutes?.value).toBe(15);
  await page.getByRole('button',{name:'Save',exact:true}).click();
  await expect(page.locator('#settings-message')).toHaveText('Setting saved');
  expect((await settings()).effective.feed_refresh_minutes?.value).toBe(25);
  for(const value of ['0','1441','1.5','']){
    await input.fill(value);
    await page.getByRole('button',{name:'Save',exact:true}).click();
    expect(await input.evaluate((node:HTMLInputElement)=>node.validity.valid)).toBe(false);
    expect((await settings()).effective.feed_refresh_minutes?.value).toBe(25);
  }
  await page.getByRole('button',{name:'Discard Change',exact:true}).click();
  await expect(input).toHaveValue('25');
  await page.locator('#settings-close').click();
  await page.locator('#settings-open').click();
  await page.getByRole('button',{name:'Feed Refresh Interval',exact:true}).click();
  await expect(input).toHaveValue('25');
  await page.getByRole('button',{name:'Reset',exact:true}).click();
  await expect(input).toHaveValue('15');
  expect((await settings()).overrides.feed_refresh_minutes).toBeUndefined();
  await page.screenshot({path:artifactPath('feed-refresh-interval-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:artifactPath('feed-refresh-interval-mobile.png'),fullPage:true});
  expect(await page.locator('#settings-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
});

test('delayed interval save keeps newer input after navigation', {timeout:20000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  let release:()=>void=()=>{};
  const held=new Promise<void>(resolve=>{release=resolve;});
  t.after(()=>release());
  await page.route('**/api/v3/settings/oko/global',async route=>{
    if(route.request().method()!=='PATCH')return route.continue();
    const response=await route.fetch();
    await held;
    await route.fulfill({response});
  });
  await page.goto(origin+'/#token='+token);
  await page.locator('#settings-open').click();
  await page.getByRole('button',{name:'Feed Refresh Interval',exact:true}).click();
  const input=page.getByRole('spinbutton',{name:'Interval (minutes)',exact:true});
  await input.fill('30');
  await page.getByRole('button',{name:'Save',exact:true}).click();
  await expect.poll(async()=>{const result=await api('settings/oko/global');return result.scope==='oko'?result.effective.feed_refresh_minutes.value:null;}).toBe(30);
  await input.fill('45');
  await page.locator('#settings-close').click();
  release();
  await page.locator('#settings-open').click();
  await page.getByRole('button',{name:'Feed Refresh Interval',exact:true}).click();
  await expect(input).toHaveValue('45');
  await expect(page.getByRole('button',{name:'Save',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Discard Change',exact:true}).click();
  await expect(input).toHaveValue('30');
});
