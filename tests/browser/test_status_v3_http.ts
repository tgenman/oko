import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';
import {reader as fixtureReader} from './support';

test('domain status opens the real processor form and retains an expanded finished problem',{timeout:20000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('v3/settings/oko/global','PATCH',{theses:true});
 const requests:string[]=[];page.on('request',request=>requests.push(new URL(request.url()).pathname));
 await page.goto(origin+'/#token='+token);
 await expect(page.locator('#status-toggle')).toContainText('Problems');
 await page.locator('#status-toggle').click();
 const problem=page.locator('.status-problem').first();
 const id=await problem.getAttribute('data-problem-id');
 await page.screenshot({path:artifactPath('status-v3-current-desktop.png')});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:artifactPath('status-v3-current-mobile.png')});
 await page.setViewportSize({width:1280,height:900});
 await problem.locator('summary').click();
 await expect(problem.locator('details')).toHaveAttribute('open','');
 await problem.getByRole('button',{name:'Configure Model',exact:true}).click();
 await expect(page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','codex');
 await page.locator('#settings-close').click();
 await api('v3/processors/codex','PUT',{filter:{model:'synthetic',reasoning_effort:'low',fast:false},theses:{model:'synthetic',reasoning_effort:'low',fast:false}});
 const finished=page.locator('[data-problem-id="'+id+'"]');
 await expect(finished).toContainText('Resolved');
 await expect(finished.locator('details')).toHaveAttribute('open','');
 await expect(finished).toHaveAttribute('data-section','action');
 await expect(page.locator('#status-toggle')).toHaveText('Status');
 await page.screenshot({path:artifactPath('status-v3-desktop.png')});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:artifactPath('status-v3-mobile.png')});
 expect(requests).toContain('/api/v3/status');
 expect(requests).not.toContain('/api/status');
});

test('confirmed command failure is retained until its actual draft is discarded',{timeout:20000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 const config={filter:{model:'synthetic',reasoning_effort:'low',fast:false},theses:{model:'synthetic',reasoning_effort:'low',fast:false}};
 await page.goto(origin+'/#token='+token);
 await expect.poll(async()=>{const state=await api('v3/status');return !!state&&typeof state==='object'&&'work_allowed'in state&&state.work_allowed===true;}).toBe(true);
 await api('v3/processors/codex','PUT',config);
 await page.locator('#settings-open').click();
 await page.locator('#settings-dialog [data-settings-target="models"]').click();
 await page.locator('#settings-dialog [data-settings-target="codex"]').click();
 await page.route('**/api/v3/processors/codex',async route=>{if(route.request().method()==='PUT')await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({code:'processor_unavailable',message:'Synthetic confirmed rejection',details:{retry_at:null},diagnostic_id:null,command_id:null})});else await route.continue();});
 await page.locator('#codex-save').click();
 await expect(page.locator('#status-toggle')).toHaveText('Problems · 1');
 await page.locator('#codex-discard').click();
 await expect(page.locator('#status-toggle')).toHaveText('Status');
 const snapshot=await api('v3/status');
 expect(snapshot).toHaveProperty('problems.count',0);
 expect(snapshot).toHaveProperty('problems.recent.0.outcome','irrelevant');
});

test('lost command response stays unknown until receipt reconciliation and is never replayed',{timeout:20000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 const config={filter:{model:'synthetic',reasoning_effort:'low',fast:false},theses:{model:'synthetic',reasoning_effort:'low',fast:false}};
 await page.goto(origin+'/#token='+token);
 await expect.poll(async()=>{const state=await api('v3/status');return !!state&&typeof state==='object'&&'work_allowed'in state&&state.work_allowed===true;}).toBe(true);
 await api('v3/processors/codex','PUT',config);
 await page.locator('#settings-open').click();
 await page.locator('#settings-dialog [data-settings-target="models"]').click();
 await page.locator('#settings-dialog [data-settings-target="codex"]').click();
 let puts=0;
 await page.route('**/api/v3/command-observations',route=>route.abort('failed'));
 await page.route('**/api/v3/processors/codex',async route=>{if(route.request().method()==='PUT'){puts++;const reply=await route.fetch();expect(reply.status()).toBe(200);await route.abort('failed');}else await route.continue();});
 await page.locator('#codex-save').click();
 await expect(page.locator('#status-toggle')).toHaveText('Problems · 1');
 await page.locator('#settings-close').click();await page.locator('#status-toggle').click();
 await expect(page.locator('#status-panel')).toContainText('Action outcome unknown');
 await expect(page.locator('#status-panel')).toContainText('is not retried automatically');
 await page.unroute('**/api/v3/command-observations');
 await expect(page.locator('#status-toggle')).toHaveText('Status');
 expect(puts).toBe(1);
});

test('typed profile recovery selects the exact role',async()=>{
 const r=await fixtureReader();try{
  const baseline=await r.api('v3/status');
  if(!baseline||typeof baseline!=='object')throw new Error('Missing RuntimeStatus');
  await r.start([]);
  await r.page.evaluate(base=>readerTransport.replies.set('/api/v3/status',{body:{...base,work_allowed:true,problems:{current:[{id:'role-problem',code:'profile_unavailable',command_id:null,title:'Profile',message:'Session unavailable',consequence:'Collection paused',affected:[{kind:'telegram_profile',role:'personal'}],recovery:[{kind:'configure_telegram_profile',role:'personal'}],created_at:new Date().toISOString(),state:'current'}],recent:[],count:1,updated_at:new Date().toISOString()}}}),baseline);
  await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');await r.page.locator('#status-toggle').click();
  await r.page.getByRole('button',{name:'Configure Telegram Profile',exact:true}).click();
  await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','telegram-profiles');
  await expect(r.page.locator('#telegram-profile-personal')).toBeFocused();
  await r.page.locator('#settings-close').click();
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a status reply started before YouTube save cannot overwrite the saved connection',{timeout:20000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('v3/settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 await page.goto(origin+'/#token='+token);await page.locator('#settings-open').click();
 await page.locator('#settings-dialog [data-settings-target="youtube"]').click();
 await expect(page.locator('#youtube-mode')).toHaveValue('direct');
 let releaseOld:(()=>Promise<void>)|undefined;
 const waiting:import('playwright').Route[]=[];
 await page.route('**/api/v3/reading/check',async route=>{if(!releaseOld){const reply=await route.fetch();releaseOld=()=>route.fulfill({response:reply});}else waiting.push(route);});
 await expect.poll(()=>Boolean(releaseOld)).toBe(true);
 await page.locator('#youtube-mode').selectOption('proxy');
 await page.locator('#youtube-address').fill('http://127.0.0.1:9999');
 await page.locator('#youtube-save').click();await expect(page.locator('#youtube-message')).toHaveText('Settings saved');
 await releaseOld!();
 await expect.poll(()=>waiting.length).toBeGreaterThan(0);
 await page.locator('#settings-dialog .settings-back').click();
 await expect(page.locator('#youtube-short-status')).toHaveText('Via Proxy');
 for(const route of waiting)await route.abort('aborted');
});
