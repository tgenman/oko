import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';
test('NUL identity renders status card and does not initiate commands',{timeout:20000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('v3/settings/oko/global','PATCH',{theses:true});
 const id='legacy-unaddressable-\0abc';
 let sends=0;
 page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/problems/'))sends++;});
 await page.route('**/api/v3/status**',async route=>{const reply=await route.fetch();if(reply.status()!==200){await route.fulfill({response:reply});return;}const obj=await reply.json();const ps=obj.problems;if(Array.isArray(ps))ps.forEach((p:any)=>p.id=id);else if(ps){for(const p of [...ps.current,...ps.recent]){p.id=id;p.recovery=[];p.affected=[];}}await route.fulfill({response:reply,json:obj});});
 await page.goto(origin+'/#token='+token);await expect(page.locator('#status-toggle')).toContainText('Problems');await page.locator('#status-toggle').click();
 const card=page.locator('.status-problem').first();await expect(card).toBeVisible();expect(await card.getAttribute('data-problem-id')).toBe(id);await card.locator('summary').click();await expect(card.locator('details')).toHaveAttribute('open','');await page.screenshot({path:artifactPath('nul-surrogate.png')});expect(sends).toBe(0);
});
