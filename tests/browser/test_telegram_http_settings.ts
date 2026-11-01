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

// Reset + newer-input/ABA combinations run against MenusOwner in Node.
