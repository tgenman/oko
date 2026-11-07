import {test} from 'node:test';
import {expect} from 'playwright/test';
import {reader,material} from './support';
import {artifactPath} from './artifacts';

test('touch hold opens card, feed and folder menus without selecting them', {timeout:30000},async()=>{
 const r=await reader();try{
  await r.page.setViewportSize({width:390,height:844});await r.start([material('a'),material('b')]);await r.page.bringToFront();
  const c=await r.page.context().newCDPSession(r.page);await c.send('Emulation.setTouchEmulationEnabled',{enabled:true});
  const touch=async(selector:string,kind:'hold'|'tap'|'move'|'native'|'cancel')=>{
   const row=r.page.locator(selector);await expect(row).toBeVisible();const box=(await row.boundingBox())!;
   const x=box.x+Math.min(box.width/2,100),y=box.y+box.height/2;
   await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
   if(kind==='native')await row.dispatchEvent('contextmenu',{bubbles:true,cancelable:true,clientX:x,clientY:y});
   if(kind==='move')await c.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+40,y}]});
   await r.page.waitForTimeout(kind==='tap'?80:700);
   if(kind==='hold'&&selector==='[data-card=\"a\"]')await r.page.locator('#all-folders').focus();
   await c.send('Input.dispatchTouchEvent',{type:kind==='cancel'?'touchCancel':'touchEnd',touchPoints:[]});
  };
  await touch('[data-card="a"]','hold');await expect(r.page.locator('#news-context-menu')).toBeVisible();
  await expect(r.page.locator('[data-card="a"]')).toHaveAttribute('aria-pressed','false');
  await r.page.screenshot({path:artifactPath('mobile-card-context-menu.png')});await expect.poll(()=>r.page.locator('#news-context-menu [role=menuitem]:not(:disabled)').first().evaluate(el=>el===document.activeElement)).toBe(true);await r.page.keyboard.press('Escape');await expect(r.page.locator('#news-context-menu')).toHaveCount(0);
  await touch('[data-feed="feed"]','hold');await expect(r.page.locator('#feed-context-menu')).toBeVisible();
  await expect(r.page.locator('[data-feed="feed"]')).toHaveAttribute('aria-pressed','false');await r.page.mouse.click(5,5);
  await touch('[data-folder]','hold');await expect(r.page.locator('#folder-context-menu')).toBeVisible();
  await expect(r.page.locator('[data-folder]')).toHaveAttribute('aria-pressed','false');await r.page.mouse.click(5,5);
  await touch('[data-card="a"]','move');await expect(r.page.locator('#news-context-menu')).toHaveCount(0);
  await touch('[data-card="a"]','native');await expect(r.page.locator('#news-context-menu')).toBeVisible();await expect(r.page.locator('[data-card="a"]')).toHaveAttribute('aria-pressed','false');await r.page.mouse.click(5,5);
  await touch('[data-card="a"]','cancel');await expect(r.page.locator('#news-context-menu')).toBeVisible();await r.page.mouse.click(5,5);
  await r.page.locator('[data-card="a"]').focus();await r.page.keyboard.press('Enter');await expect(r.page.locator('#text')).toContainText('Текст a');
  await touch('[data-card="b"]','tap');await expect(r.page.locator('#text')).toContainText('Текст b');
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});


test('touch scroll on a card does not open a menu', {timeout:30000},async()=>{
 const r=await reader();try{
  await r.page.setViewportSize({width:390,height:844});await r.start(Array.from({length:30},(_,i)=>material('item-'+i)));
  const c=await r.page.context().newCDPSession(r.page);await c.send('Emulation.setTouchEmulationEnabled',{enabled:true});
  const row=r.page.locator('[data-card="item-4"]'),box=(await row.boundingBox())!;
  const x=Math.min(380,box.x+80),y=box.y+box.height/2;
  await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
  for(let offset=20;offset<=200;offset+=20)await c.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y-offset}]});
  await c.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await expect.poll(()=>r.page.locator('#card-scroll').evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  await expect(r.page.locator('#news-context-menu')).toHaveCount(0);
 }finally{await r.close();}
});


test('a second touch cancels an unfinished hold', {timeout:30000},async()=>{
 const r=await reader();try{
  await r.page.setViewportSize({width:390,height:844});await r.start([material('a')]);
  const c=await r.page.context().newCDPSession(r.page);await c.send('Emulation.setTouchEmulationEnabled',{enabled:true});
  const box=(await r.page.locator('[data-card="a"]').boundingBox())!;const x=Math.min(380,box.x+80),y=box.y+box.height/2;
  await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y,id:1}]});
  await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y,id:1},{x:5,y:5,id:2}]});
  await r.page.waitForTimeout(700);await expect(r.page.locator('#news-context-menu')).toHaveCount(0);
  await c.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});
 }finally{await r.close();}
});
