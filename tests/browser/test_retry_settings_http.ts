import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('retry delays save, reopen and reset through Settings', {timeout:25000}, async t=>{
 const r=await productionReader(t);
 await r.openReader();await r.page.locator('#settings-open').click();
 await r.page.getByRole('button',{name:'Retry Delays',exact:true}).click();
 await r.page.getByRole('button',{name:'Initial Retry Delay',exact:true}).click();
 const input=r.page.getByRole('spinbutton',{name:'Delay (seconds)',exact:true});
 await expect(input).toHaveValue('60');await input.fill('2');
 await r.page.getByRole('button',{name:'Save',exact:true}).click();
 await expect(r.page.locator('#settings-message')).toHaveText('Setting saved');
 expect((await r.api('settings/retries')).initial_seconds).toBe(2);
 await r.page.locator('#settings-close').click();await r.page.locator('#settings-open').click();
 await r.page.getByRole('button',{name:'Retry Delays',exact:true}).click();
 await r.page.getByRole('button',{name:'Maximum Retry Delay',exact:true}).click();
 await expect(input).toHaveValue('3600');await input.fill('8');
 await r.page.getByRole('button',{name:'Save',exact:true}).click();
 await expect(r.page.locator('#settings-message')).toHaveText('Setting saved');
 expect((await r.api('settings/retries')).max_seconds).toBe(8);
 await r.page.getByRole('button',{name:'Reset',exact:true}).click();await expect(input).toHaveValue('3600');
 await r.page.screenshot({path:artifactPath('retry-settings-desktop.png')});
 await r.page.setViewportSize({width:390,height:844});await r.page.screenshot({path:artifactPath('retry-settings-mobile.png')});
 expect(await r.page.locator('#settings-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
});

test('late retry setting response preserves a newer draft', {timeout:20000}, async t=>{
 const r=await productionReader(t);let release=()=>{};const held=new Promise<void>(resolve=>{release=resolve;});t.after(()=>release());
 await r.page.route('**/api/v3/settings/oko/global',async route=>{if(route.request().method()!=='PATCH')return route.continue();const response=await route.fetch();await held;await route.fulfill({response});});
 await r.openReader();await r.page.locator('#settings-open').click();
 await r.page.getByRole('button',{name:'Retry Delays',exact:true}).click();await r.page.getByRole('button',{name:'Initial Retry Delay',exact:true}).click();
 const input=r.page.getByRole('spinbutton',{name:'Delay (seconds)',exact:true});await expect(input).toHaveValue('60');
 await input.fill('2');await r.page.getByRole('button',{name:'Save',exact:true}).click();await expect.poll(async()=>(await r.api('settings/retries')).initial_seconds).toBe(2);
 await input.fill('3');await r.page.locator('#settings-close').click();release();await r.page.locator('#settings-open').click();
 await r.page.getByRole('button',{name:'Retry Delays',exact:true}).click();await r.page.getByRole('button',{name:'Initial Retry Delay',exact:true}).click();
 await expect(input).toHaveValue('3');await r.page.getByRole('button',{name:'Discard Change',exact:true}).click();await expect(input).toHaveValue('2');
});
