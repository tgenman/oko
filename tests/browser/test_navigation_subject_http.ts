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
