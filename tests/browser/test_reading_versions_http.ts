import {test} from 'node:test';
import assert from 'node:assert/strict';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {addSources} from './connections';
import type {ReadingCheckResponse} from '../../frontend/src/api/generated/types.gen';

test('unchanged reading checks transmit versions and opening bypasses the poll', {timeout:30000}, async t=>{
  const {page,api,origin,token}=await productionReader(t);
  await api('settings/oko/global','PATCH',{theses:false});
  await addSources(api,[['https://a.test/feed','Synthetic','Folder']]);
  const paths:string[]=[],frames:ReadingCheckResponse[]=[];
  page.on('request',request=>{paths.push(new URL(request.url()).pathname);});
  page.on('response',async response=>{if(new URL(response.url()).pathname==='/api/v3/reading/check'&&response.ok())try{frames.push(await response.json());}catch{/* A response cancelled by fixture teardown is not an application failure. */}});
  await page.goto(origin+'/#token='+token);
  await expect(page.locator('#cards button')).toHaveCount(1);
  await expect.poll(async()=>{const cards=await api('cards');return cards.length===1&&cards[0].availability==='available'&&!cards[0].loading;}).toBe(true);
  await expect(page.locator('#cards .card-kind')).toHaveCount(0);
  const before=frames.length;
  // A complete card can precede the final Runtime startup/status transition.
  // Require two actually unchanged polls within the existing wait budget.
  await expect.poll(()=>{
    if(frames.length<before+2)return false;
    return frames.slice(-2).every(frame=>frame.state==='current'&&frame.resources.every(resource=>resource.state==='unchanged'));
  }).toBe(true);
  const quiet=frames.slice(-2);
  for(const frame of quiet){assert.equal(frame.state,'current');if(frame.state==='current')assert.ok(frame.resources.every(resource=>resource.state==='unchanged'));}
  assert.ok(!paths.includes('/api/v3/cards'));
  assert.ok(!paths.includes('/api/v3/reading-composition'));
  // A hung periodic request must not delay a direct open or its first paint.
  let release:(()=>void)|undefined;
  await page.route('**/api/v3/reading/check',async route=>{await new Promise<void>(resolve=>{release=resolve;});await route.continue().catch(()=>{});});
  await expect.poll(()=>!!release).toBe(true);
  await page.locator('#cards button').click();
  await expect(page.locator('#text')).toContainText('Открылась библиотека');
  assert.equal(paths.filter(path=>/^\/api\/v3\/reading\/materials\/[^/]+$/.test(path)).length,1);
  release?.();
  await page.unroute('**/api/v3/reading/check');
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
  await page.route('**/api/v3/reading/check',route=>route.abort('failed'));
  await expect(page.locator('#progress')).toContainText('No connection');
  const cards=await api('cards');
  await api('materials/'+cards[0].id+'/viewed','POST');
  const remaining=catalog.feeds.find(feed=>feed.id===cards[1].feed_id)!;
  await api('feeds/'+remaining.id,'PATCH',{title:'Renamed during disconnect'});
  await expect(other.locator('#cards button')).toHaveCount(1);
  await expect(other.locator('#cards button')).toContainText('Renamed during disconnect');
  await expect(page.locator('#cards button')).toHaveCount(2);
  await page.unroute('**/api/v3/reading/check');
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
