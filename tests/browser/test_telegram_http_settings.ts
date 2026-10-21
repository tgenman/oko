import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('Telegram request limit saves, validates decimals, reopens and resets', {timeout:20000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await page.goto(origin+'/#token='+token);
 await page.locator('#settings-open').click();
 await page.getByRole('button',{name:'Telegram Request Limit',exact:true}).click();
 const input=page.getByRole('spinbutton',{name:'Requests per second',exact:true});
 await expect(input).toHaveValue('2');
 await input.fill('0.5');
 expect((await api('settings/telegram-http')).value).toBe(2);
 await page.getByRole('button',{name:'Save',exact:true}).click();
 await expect(page.locator('#settings-message')).toHaveText('Setting saved');
 expect((await api('settings/telegram-http')).value).toBe(.5);
 for(const value of ['0','10.1','0.09','']){
  await input.fill(value);await page.getByRole('button',{name:'Save',exact:true}).click();
  expect(await input.evaluate((node:HTMLInputElement)=>node.validity.valid)).toBe(false);
  expect((await api('settings/telegram-http')).value).toBe(.5);
 }
 await page.getByRole('button',{name:'Discard Change',exact:true}).click();
 await expect(input).toHaveValue('0.5');
 await page.locator('#settings-close').click();await page.locator('#settings-open').click();
 await page.getByRole('button',{name:'Telegram Request Limit',exact:true}).click();
 await expect(input).toHaveValue('0.5');
 await page.getByRole('button',{name:'Reset',exact:true}).click();
 await expect(input).toHaveValue('2');
 expect((await api('settings/telegram-http')).origin.scope).toBe('default');
 await page.screenshot({path:artifactPath('telegram-http-setting-desktop.png')});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:artifactPath('telegram-http-setting-mobile.png')});
 expect(await page.locator('#settings-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
});

test('delayed Telegram limit save preserves a newer draft across navigation', {timeout:20000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 let release=()=>{};const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 await page.route('**/api/v3/settings/oko/global',async route=>{
  if(route.request().method()!=='PATCH')return route.continue();
  const response=await route.fetch();await held;await route.fulfill({response});
 });
 await page.goto(origin+'/#token='+token);await page.locator('#settings-open').click();
 await page.getByRole('button',{name:'Telegram Request Limit',exact:true}).click();
 const input=page.getByRole('spinbutton',{name:'Requests per second',exact:true});
 await input.fill('4');await page.getByRole('button',{name:'Save',exact:true}).click();
 await expect.poll(async()=>(await api('settings/telegram-http')).value).toBe(4);
 await input.fill('1');await page.locator('#settings-close').click();release();
 await page.locator('#settings-open').click();await page.getByRole('button',{name:'Telegram Request Limit',exact:true}).click();
 await expect(input).toHaveValue('1');
 await page.getByRole('button',{name:'Discard Change',exact:true}).click();await expect(input).toHaveValue('4');
});

test('discarding a failed Telegram limit draft clears its command problem', {timeout:20000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await page.route('**/api/v3/settings/oko/global',async route=>{
  if(route.request().method()!=='PATCH')return route.continue();
  await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({code:'action_invalid',message:'Synthetic failure',details:{reason:'invalid_state'},diagnostic_id:null,command_id:route.request().headers()['x-oko-action']})});
 });
 await page.goto(origin+'/#token='+token);await page.locator('#settings-open').click();
 await page.getByRole('button',{name:'Telegram Request Limit',exact:true}).click();
 const input=page.getByRole('spinbutton',{name:'Requests per second',exact:true});
 await input.fill('4');await page.getByRole('button',{name:'Save',exact:true}).click();
 await expect(page.locator('#settings-message')).toHaveText('Synthetic failure');
 await expect.poll(async()=>(await api('status')).problems.current.filter(p=>p.code==='command_failed').length).toBe(1);
 await page.getByRole('button',{name:'Discard Change',exact:true}).click();
 await expect(input).toHaveValue('2');
 await expect.poll(async()=>(await api('status')).problems.current.filter(p=>p.code==='command_failed').length).toBe(0);
});
