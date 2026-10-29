import {test} from 'node:test';
import assert from 'node:assert/strict';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';

test('unchanged navigation and status return conditional 304s and opening bypasses the poll', {timeout:30000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  await api('settings/oko/global','PATCH',{theses:false});
  await addSources(api,[['https://a.test/feed','Synthetic','Folder']]);
  const paths:string[]=[],frames:{path:string;status:number;validator:string|undefined}[]=[];
  page.on('request',request=>{paths.push(new URL(request.url()).pathname);});
  page.on('response',response=>{
    const path=new URL(response.url()).pathname;
    if(['/api/v3/status','/api/v3/navigation'].includes(path))frames.push({path,status:response.status(),validator:response.request().headers()['if-none-match']});
  });
  await page.goto(origin+'/#token='+token);
  await expect(page.locator('#cards button')).toHaveCount(1);
  await expect.poll(async()=>{const cards=(await api('cards')).items;return cards.length===1&&!cards[0].loading;}).toBe(true);
  await expect(page.locator('#cards .card-kind')).toHaveCount(0);
  const before=frames.length;
  // Each subject independently confirms two unchanged conditional polls.
  await expect.poll(()=>['/api/v3/status','/api/v3/navigation'].every(path=>
    frames.slice(before).filter(frame=>frame.path===path&&frame.status===304&&!!frame.validator).length>=2
  )).toBe(true);
  assert.ok(paths.includes('/api/v3/cards'));
  assert.ok(!paths.includes('/api/v3/reading-composition'));
  // A hung periodic request must not delay a direct open or its first paint.
  let release:(()=>void)|undefined;
  await page.route('**/api/v3/status',async route=>{await new Promise<void>(resolve=>{release=resolve;});await route.continue().catch(()=>{});});
  await expect.poll(()=>!!release).toBe(true);
  await page.locator('#cards button').click();
  await expect(page.locator('#text')).toContainText('Открылась библиотека');
  assert.equal(paths.filter(path=>/^\/api\/v3\/publication\/[^/]+$/.test(path)).length,1);
  release?.();
  await page.unroute('**/api/v3/status');
});

test('tabs recover missed reading changes and reconcile a local viewed action', {timeout:30000}, async t=>{
  const {page,context,api,origin,token}=await productionReader(t);
  await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
  const catalog=await addSources(api,[['https://a.test/feed','Alpha','Folder'],['https://b.test/feed','Beta','Other']]);
  await page.goto(origin+'/#token='+token);
  const other=await context.newPage();await other.goto(origin+'/#token='+token);
  await expect(page.locator('#cards button')).toHaveCount(2);
  await expect(other.locator('#cards button')).toHaveCount(2);
  await page.locator('#cards button').first().click();
  await expect(page.locator('#text')).toContainText('Открылась библиотека');
  await page.route('**/api/v3/status',route=>route.abort('failed'));
  await expect(page.locator('#collection-progress')).toContainText('Нет связи');
  const cards=(await api('cards')).items;
  await api('materials/'+cards[0].id+'/viewed','POST');
  const remaining=catalog.feeds.find(feed=>feed.id===cards[1].feed_id)!;
  await api('feeds/'+remaining.id,'PATCH',{title:'Renamed during disconnect'});
  await expect(other.locator('#cards button')).toHaveCount(1);
  await expect(other.locator('#cards button')).toContainText('Renamed during disconnect');
  await expect(page.locator('#cards button')).toHaveCount(2);
  await page.unroute('**/api/v3/status');
  await expect(page.locator('#cards button')).toHaveCount(1);
  await expect(page.locator('#cards button')).toContainText('Renamed during disconnect');
  await other.locator('#cards button').click();
  await expect(other.locator('#text')).toContainText('Открылась библиотека');
  await other.locator('#viewed').click();
  await expect(page.locator('#cards button')).toHaveCount(0);
  await expect(other.locator('#cards button')).toHaveCount(0);
});

test('viewed in another tab deletes server content while the loaded tab retains its text and media until reload',{timeout:25000},async t=>{
  const {page,context,api,origin,token}=await productionReader(t);
  await api('settings/oko/global','PATCH',{theses:false,prompt_filters:''});
  await addSources(api,[['https://a.test/feed','Alpha','Folder']]);
  await page.goto(origin+'/#token='+token);await expect(page.locator('#cards button')).toHaveCount(1);
  const id=await page.locator('#cards button').getAttribute('data-card');
  await page.locator('#cards button').click();await expect(page.locator('#text')).toContainText('Открылась библиотека');await expect(page.locator('#images img')).toHaveAttribute('src',/^blob:/);
  const text=await page.locator('#text').textContent(),image=await page.locator('#images img').getAttribute('src');
  const other=await context.newPage();await other.goto(origin+'/#token='+token);await expect(other.locator('#cards button')).toHaveCount(1);await other.locator('#cards button').click();await expect(other.locator('#text')).toHaveText(text!);await other.locator('#viewed').click();
  await expect(page.locator('#cards button')).toHaveCount(0);await expect(page.locator('#material-retained')).toBeVisible();await expect(page.locator('#text')).toHaveText(text!);await expect(page.locator('#images img')).toHaveAttribute('src',image!);await expect(page.locator('#viewed')).toBeDisabled();
  const response=await page.request.get(origin+'/api/v3/materials/'+id,{headers:{Authorization:'Bearer '+token}});assert.equal(response.status(),404);
  await page.reload();await expect(page.locator('#cards button')).toHaveCount(0);await expect(page.locator('#material')).toBeHidden();
});
