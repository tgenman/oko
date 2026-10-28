import {test} from 'node:test';
import assert from 'node:assert/strict';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';

test('opening and updating a publication use one conditional GET', {timeout:30000}, async t=>{
 const r=await productionReader(t);
 await r.api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 await addSources(r.api,[['https://a.test/feed','Synthetic','Folder']]);
 const urls:string[]=[];
 r.page.on('request',q=>{if(q.method()==='GET')urls.push(new URL(q.url()).pathname);});
 await r.page.goto(r.origin+'/#token='+r.token);
 await expect(r.page.locator('#cards [data-card]')).toHaveCount(1);
 await r.page.locator('#cards [data-card]').first().click();
 await expect(r.page.locator('#text')).toContainText('Открылась библиотека');
 await expect.poll(()=>urls.filter(u=>u.startsWith('/api/v3/publication/')).length).toBeGreaterThanOrEqual(2);
 assert.equal(urls.filter(u=>u.startsWith('/api/v3/reading/materials/')).length,0);
});


test('a viewed publication retains already-open text when the GET becomes unavailable', {timeout:30000}, async t=>{
 const r=await productionReader(t);
 await r.api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
 await addSources(r.api,[['https://a.test/feed','Synthetic','Folder']]);
 await r.page.goto(r.origin+'/#token='+r.token);
 const card=r.page.locator('#cards [data-card]').first();await expect(card).toBeVisible();
 const id=await card.getAttribute('data-card');await card.click();
 await expect(r.page.locator('#text')).toContainText('Открылась библиотека');
 await r.api('materials/'+id+'/viewed','POST');
 await expect(r.page.locator('#material-retained')).toBeVisible();
 await expect(r.page.locator('#text')).toContainText('Открылась библиотека');
 await expect(r.page.locator('#viewed')).toBeDisabled();
});
