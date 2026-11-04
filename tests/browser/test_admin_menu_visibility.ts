import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('Admin menu remains clickable at narrow viewport', {timeout:30000}, async t=>{
 const {page,origin,token}=await productionReader(t);
 await page.setViewportSize({width:390,height:844});
 await page.goto(origin+'/#token='+token);
 await page.locator('#app-menu summary').click();
 const link=page.locator('#admin-link');
 await expect(link).toBeVisible();
 await expect.poll(()=>link.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));})).toBe(true);
 await page.screenshot({path:artifactPath('admin-menu-mobile.png')});
 await page.locator('.reader-toolbar-scroll').evaluate(el=>{const button=el.querySelector('#app-menu summary')!.getBoundingClientRect();const bounds=el.getBoundingClientRect();el.scrollLeft-=bounds.right-button.right+button.width/2;});
 await expect(link).toBeVisible();
 await expect.poll(()=>link.evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;})).toBe(true);
 await page.locator('.reader-toolbar-scroll').evaluate(el=>{el.scrollLeft=0;});
 await expect(page.locator('#app-menu')).not.toHaveAttribute('open','');
 await page.locator('#app-menu summary').click();
 await expect(link).toBeVisible();
 await link.click();
 await expect(page.locator('#admin-page')).toBeVisible();
});
