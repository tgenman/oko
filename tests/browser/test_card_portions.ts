import {test} from 'node:test';
import {reader,material,expect} from './support';
import {artifactPath} from './artifacts';

const items=()=>Array.from({length:105},(_,i)=>material('item-'+String(i).padStart(3,'0'),{published:1000-i}));

test('card portions preserve full count, keyboard continuation and returning scroll position',async()=>{
 const r=await reader();try{
  await r.start(items());
  await expect(r.page.locator('#count')).toHaveText('105');
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(40);
  await r.page.locator('[data-card="item-039"]').focus();
  await r.page.keyboard.press('ArrowDown');
  await expect(r.page.locator('[data-card="item-040"]')).toBeFocused();
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(80);
  const top=await r.page.locator('#card-scroll').evaluate(n=>n.scrollTop);
  await r.page.locator('[data-folder="f"]').click();
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(40);
  await r.page.locator('#all-folders').click();
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(80);
  expect(await r.page.locator('#card-scroll').evaluate(n=>n.scrollTop)).toBe(top);
  await r.page.locator('#card-scroll').evaluate(n=>{n.scrollTop=n.scrollHeight;});
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(105);
  await r.page.screenshot({path:artifactPath('card-portions.png')});
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('below captures unloaded tail titles and does not add arrivals during preparation',async()=>{
 const r=await reader();try{
  await r.start(items());
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards',true));
  await r.page.locator('[data-card="item-001"]').click({button:'right'});
  await r.page.getByRole('menuitem',{name:'Mark Below as Read',exact:true}).click();
  await expect(r.page.locator('#news-read-confirm')).toBeDisabled();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards'));
  await r.page.evaluate(()=>{const t=readerTransport,rows=t.replies.get('/api/v3/cards')!.body!;t.release('/api/v3/cards',rows);rows.unshift({...rows[0],id:'fresh',title:'Fresh'});});
  await expect(r.page.locator('#news-read-dialog li')).toHaveCount(103);
  await expect(r.page.locator('#news-read-dialog li').last()).toHaveText('item-104');
  await expect(r.page.locator('#news-read-confirm')).toBeEnabled();
  expect(await r.page.locator('#news-read-dialog li').allTextContents()).not.toContain('Fresh');
  expect(await r.page.locator('#cards [data-card]').count()).toBeLessThan(105);
  await r.page.locator('#news-read-cancel').click();
  await expect(r.page.locator('#count')).toHaveText('106');
  await r.page.locator('#card-scroll').evaluate(n=>{n.scrollTop=n.scrollHeight;});
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(81);
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a cancelled tail preparation cannot reopen the dialog or enable partial confirmation',async()=>{
 const r=await reader();try{
  await r.start(items());
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards',true));
  await r.page.locator('[data-card="item-001"]').click({button:'right'});
  await r.page.getByRole('menuitem',{name:'Mark Below as Read',exact:true}).click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards'));
  await r.page.locator('#news-read-cancel').click();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards',readerTransport.replies.get('/api/v3/cards')!.body));
  await expect(r.page.locator('#news-read-dialog')).toHaveCount(0);
  await r.page.locator('[data-folder="f"]').click();
  await expect(r.page.locator('#list-title')).toHaveText('Тема');
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});


test('new arrivals retain the last demanded card and its scroll anchor',async()=>{
 const r=await reader();try{
  await r.start(items());
  // Keep the next portion pending while a new leading card changes composition.
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards?sort=newest',true));
  await r.page.locator('[data-card="item-039"]').focus();
  const top=await r.page.locator('[data-card="item-039"]').evaluate(n=>n.getBoundingClientRect().top);
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest'));
  await r.page.evaluate(()=>{const t=readerTransport,rows=t.replies.get('/api/v3/cards')!.body!;rows.unshift(...Array.from({length:60},(_,i)=>({...rows[0],id:'fresh-'+i,title:'Fresh '+i})));t.release('/api/v3/cards?sort=newest',rows);});
  await expect(r.page.locator('#count')).toHaveText('165');
  await expect(r.page.locator('[data-card="item-039"]')).toBeFocused();
  expect(await r.page.locator('[data-card="item-039"]').evaluate(n=>n.getBoundingClientRect().top)).toBe(top);
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});


test('late keyboard continuation does not steal a newer focus',async()=>{
 const r=await reader();try{
  await r.start(items());
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards?sort=newest',true));
  await r.page.locator('[data-card="item-039"]').focus();await r.page.keyboard.press('ArrowDown');
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest'));
  await r.page.locator('[data-card="item-002"]').focus();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards?sort=newest',readerTransport.replies.get('/api/v3/cards')!.body));
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(80);
  await expect(r.page.locator('[data-card="item-002"]')).toBeFocused();
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});


test('replacement refresh renders a cached portion after an aborted request',async()=>{
 const r=await reader();try{
  await r.start(items());
  await r.page.locator('[data-card="item-001"]').click({button:'right'});
  await r.page.getByRole('menuitem',{name:'Mark Below as Read',exact:true}).click();
  await expect(r.page.locator('#news-read-dialog li')).toHaveCount(103);
  await r.page.locator('#news-read-cancel').click();
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/cards?sort=newest',true));
  await r.page.locator('#card-scroll').evaluate(n=>{n.scrollTop=n.scrollHeight;});
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/cards?sort=newest'));
  await r.page.evaluate(()=>{readerTransport.held.delete('/api/v3/cards?sort=newest');const channel=new BroadcastChannel('oko-reader');channel.postMessage({kind:'folder-viewed',folder:'f'});channel.close();});
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(80);
  await r.page.evaluate(()=>readerTransport.release('/api/v3/cards?sort=newest',readerTransport.replies.get('/api/v3/cards')!.body));
  await expect(r.page.locator('#cards [data-card]')).toHaveCount(80);
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
