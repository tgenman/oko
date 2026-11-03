import {test} from 'node:test';
import assert from 'node:assert/strict';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {openAdmin,openReading} from './admin-navigation';
import {artifactPath} from './artifacts';

test('admin direct URL, navigation, power and status use the real server without browser leases',{timeout:45000},async t=>{
 const {page,context,origin,token,api}=await productionReader(t);
 const sessions:string[]=[];context.on('request',r=>{if(r.url().includes('/client-sessions'))sessions.push(r.url());});
 await page.goto(origin+'/admin#token='+token);
 await expect(page.locator('#admin-page')).toBeVisible();await expect(page.locator('#stop')).toBeEnabled();
 await expect(page.locator('#reading-page')).toBeHidden();
 await page.locator('#stop').click();await expect(page.locator('#stop')).toHaveText('Start');await expect(page.locator('#collect')).toBeDisabled();
 await openReading(page);await expect(page.locator('#reading-page')).toBeVisible();await expect(page.locator('#admin-controls')).toBeHidden();
 await expect(page.locator('#settings-open')).toBeVisible();await expect(page.locator('#feeds-open')).toBeVisible();
 await openAdmin(page);await expect(page.locator('#stop')).toHaveText('Start');
 await page.locator('#stop').click();await expect(page.locator('#stop')).toHaveText('Stop');
 await page.locator('#status-toggle').click();await expect(page.locator('#status-panel')).toBeVisible();
 await page.locator('#status-journal-open').click();await page.locator('#status-close').click();
 await page.locator('#admin-problems').click();await expect(page.locator('#status-body')).toBeVisible();await expect(page.locator('#status-journal')).toBeHidden();
 await page.goBack();await expect(page.locator('#status-panel')).toBeHidden();await expect(page.locator('#reading-page')).toBeVisible();
 const adminHref=await page.locator('#admin-link').getAttribute('href');assert.ok(adminHref?.includes('#token='));
 const other=await context.newPage();await other.goto(new URL(adminHref!,origin).href);await expect(other.locator('#stop')).toBeEnabled();await other.close();
 await openAdmin(page);

 await page.locator('#collection-progress').click();await expect(page.locator('.collection-dialog')).toBeVisible();await page.goBack();await expect(page.locator('.collection-dialog')).toBeHidden();await openAdmin(page);
 await page.screenshot({path:artifactPath('admin-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:artifactPath('admin-mobile.png'),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.close();assert.equal((await api('status')).runtime_state,'running');assert.equal((await api('status')).work_allowed,true);
 await api('collect','POST');
 const reopened=await context.newPage();await reopened.goto(origin+'/admin#token='+token);await expect(reopened.locator('#stop')).toBeEnabled();
 assert.deepEqual(sessions,[]);
});
