import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';

test('reader obtains navigation through the subject endpoint and revalidates it', {timeout:30000}, async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 await addSources(api,[['https://a.test/feed','Alpha','Folder']]);
 const requests:{path:string;method:string;body:string|null;validator:string|null}[]=[];
 page.on('request',request=>{const path=new URL(request.url()).pathname;if(path==='/api/v3/navigation'||path==='/api/v3/reading/check')requests.push({path,method:request.method(),body:request.postData(),validator:request.headers()['if-none-match']||null});});
 await page.goto(origin+'/#token='+token);
 await expect(page.getByText('Folder',{exact:true})).toBeVisible();
 await expect.poll(()=>requests.some(r=>r.path==='/api/v3/navigation'&&r.validator!==null),{timeout:10000}).toBe(true);
 expect(requests.filter(r=>r.path==='/api/v3/reading/check').every(r=>!JSON.stringify(JSON.parse(r.body||'{}')).includes('"kind":"navigation"'))).toBe(true);
});

test('status details are fetched on demand and stop polling when the panel closes',{timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false});
 const details:string[]=[],checks:string[]=[];
 page.on('request',r=>{const path=new URL(r.url()).pathname;if(path==='/api/v3/status/details')details.push(path);if(path==='/api/v3/reading/check')checks.push(path);});
 await page.goto(origin+'/#token='+token);
 await expect(page.locator('#status-toggle')).toHaveText('Status');
 await page.waitForTimeout(1200);
 expect(details).toHaveLength(0);
 await page.locator('#status-toggle').click();
 await expect.poll(()=>details.length).toBeGreaterThan(0);
 await expect(page.locator('#status-summary')).toHaveText('No problems · ready to read');
 await page.locator('#status-close').click();
 const before=details.length;
 await page.waitForTimeout(1500);
 expect(details.length).toBe(before);
 expect(checks).toHaveLength(0);
});

test('held stale details cannot block or override command admission from status',{timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:false});
 await page.goto(origin+'/#token='+token);
 await expect(page.locator('#collect')).toBeEnabled();
 let release:(()=>Promise<void>)|undefined;
 await page.route('**/api/v3/status/details',async route=>{
  const response=await route.fetch();
  if(!release)await new Promise<void>(resolve=>{release=async()=>{await route.fulfill({response});resolve();};});
  else await route.fulfill({response});
 });
 await page.locator('#status-toggle').click();
 await expect.poll(()=>!!release).toBe(true);
 await api('power','PUT',{enabled:false});
 await expect(page.locator('#stop')).toHaveAttribute('data-power','start');
 await expect(page.locator('#collect')).toBeDisabled();
 await release!();
 await expect(page.locator('#collect')).toBeDisabled();
 await expect(page.locator('#add-open')).toBeDisabled();
});

test('storage replacement clears old recovery actions even when new details fail',{timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:true});
 await page.goto(origin+'/#token='+token);
 await expect(page.locator('#status-toggle')).toContainText('Problems');
 await page.locator('#status-toggle').click();
 await expect(page.locator('.status-problem').first()).toBeVisible();
 const replacement='4f003741-d0c7-4e62-8d12-55fb652f404a';
 await page.route('**/api/v3/status/details',route=>route.fulfill({status:503,json:{code:'storage_unavailable',message:'Synthetic unavailable',details:{retry_at:null},diagnostic_id:null,command_id:null}}));
 for(const path of ['status','navigation'])await page.route('**/api/v3/'+path,async route=>{
  const headers={...route.request().headers()};delete headers['if-none-match'];
  const response=await route.fetch({headers}),body=await response.json();
  if(path==='status')body.problems=[];
  await route.fulfill({response,json:body,headers:{...response.headers(),'x-oko-storage-id':replacement,etag:'"replacement"'}});
 });
 await expect(page.locator('#status-toggle')).toHaveText('Status');
 await expect(page.locator('.status-problem')).toHaveCount(0);
 await expect(page.locator('.status-action')).toHaveCount(0);
});

test('opening Status applies details already cached by settings after a 304',{timeout:30000},async t=>{
 const {page,api,origin,token}=await productionReader(t);
 await api('settings/oko/global','PATCH',{theses:true});
 const replies:number[]=[];
 page.on('response',r=>{if(new URL(r.url()).pathname==='/api/v3/status/details')replies.push(r.status());});
 await page.goto(origin+'/#token='+token);
 await expect(page.locator('#status-toggle')).toContainText('Problems');
 await page.locator('#settings-open').click();
 await expect.poll(()=>replies.includes(304),{timeout:10000}).toBe(true);
 await page.locator('#settings-close').click();
 const unchanged=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/v3/status/details'&&r.status()===304);
 await page.locator('#status-toggle').click();await unchanged;
 await expect(page.locator('.status-problem').first()).toBeVisible();
 await expect(page.getByRole('button',{name:'Configure Model',exact:true})).toBeVisible();
});
