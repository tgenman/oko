const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const {existsSync} = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {once} = require('node:events');
const {createInterface} = require('node:readline');
const {chromium, expect} = require('playwright/test');

test('production HTTP reader keeps folder/feed membership, previews, grouping and viewed actions', {timeout:45000}, async t => {
 const root=path.resolve(__dirname,'../..'), data=await fs.mkdtemp(path.join(os.tmpdir(),'oko-http-browser-'));
 const venv=path.join(root,'.venv/bin/python'), python=process.env.OKO_PYTHON || (existsSync(venv)?venv:'python3');
 const child=spawn(python,['-m','tests.support.browser_server',data],{cwd:root,stdio:['ignore','pipe','pipe']});
 let browser, stderr=''; child.stderr.on('data',chunk=>{stderr+=chunk;});
 const exit=once(child,'exit'); exit.catch(()=>{});
 const abort=()=>{child.kill('SIGTERM'); void browser?.close().catch(()=>{});};
 t.signal.addEventListener('abort',abort,{once:true});
 try {
  const lines=createInterface({input:child.stdout});
  let timer;
  const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('HTTP fixture did not announce its address')),10000);});
  let first;
  try{first=await Promise.race([once(lines,'line'),exit.then(()=>{throw new Error('HTTP fixture exited: '+stderr);}),deadline]);}finally{clearTimeout(timer);}
  const {origin,token}=JSON.parse(first[0]); lines.close();
  const headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'};
  async function api(url,method='GET',body) {
   const response=await fetch(origin+'/api/'+url,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
   assert.equal(response.status,200,await response.clone().text()); return response.json();
  }
  await expect.poll(async()=>{try{return (await fetch(origin)).status;}catch{return 0;}},{timeout:10000}).toBe(200);
  assert.equal((await fetch(origin+'/api/cards')).status,403);
  await api('settings/oko/global','PATCH',{events:false,processor:'llamacpp'});
  const plan=await api('import','POST',{opml:'<opml><body><outline text="Наука"><outline text="Альфа" xmlUrl="https://a.test/feed"/><outline text="Бета" xmlUrl="https://b.test/feed"/><outline text="Пустой" xmlUrl="https://empty.test/feed"/></outline></body></opml>'});
  browser=await chromium.launch({headless:true,executablePath:process.env.OKO_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page=await browser.newPage(), errors=[]; page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin+'/#token='+token);
  await expect.poll(async()=>{const cards=await api('cards'); return cards.length===2&&cards.every(c=>c.complete&&!c.loading);},{timeout:15000}).toBe(true);
  await expect(page.locator('#cards button')).toHaveCount(2);
  await expect(page.locator('#cards .card-snippet').first()).toHaveText('Открылась библиотека на 24 места.');
  await expect(page.locator('#cards img')).toHaveCount(2);
  await expect.poll(()=>page.locator('#cards img').evaluateAll(images=>images.every(img=>img.complete&&img.naturalWidth>0))).toBe(true);
  const alpha=plan.feeds.find(feed=>feed.title==='Альфа'), beta=plan.feeds.find(feed=>feed.title==='Бета'), empty=plan.feeds.find(feed=>feed.title==='Пустой');
  const navigation=await api('navigation'), folder=navigation.folders.find(folder=>folder.title==='Наука');
  await page.locator(`[data-folder="${folder.id}"]`).click();
  await expect(page.locator('#count-label')).toHaveText('2 карточки');
  for(const feed of [alpha,beta]){
   await page.locator(`[data-feed="${feed.id}"]`).click();
   await expect(page.locator('#cards button')).toHaveCount(1);
   await expect(page.locator('#cards .card-source')).toHaveText(feed.title);
  }
  await api('processors/llamacpp','PUT',{endpoint:'http://localhost:9931',model:'local:latest',local_execution:true});
  await api('settings/oko/global','PATCH',{events:true});
  await expect.poll(async()=>{const cards=await api('cards');return cards.length===1&&!!cards[0].event;},{timeout:15000}).toBe(true);
  for(const feed of [alpha,beta]){
   await page.locator(`[data-feed="${feed.id}"]`).click();
   await expect(page.locator('#cards button')).toHaveCount(1);
   await expect(page.locator('#cards .card-kind')).toHaveText('Событие');
   await expect(page.locator('#cards .card-source')).toContainText('Альфа');
   await expect(page.locator('#cards .card-source')).toContainText('Бета');
  }
  await page.locator(`[data-feed="${empty.id}"]`).click();
  await expect(page.locator('#cards button')).toHaveCount(0);
  await expect(page.locator('#list-status')).toHaveText('Нет материалов');
  await page.locator(`[data-folder="${folder.id}"]`).click();
  await expect(page.locator('#count-label')).toHaveText('1 карточка');
  await expect(page.locator(`[data-folder="${folder.id}"] .tree-count`)).toHaveText('2');
  await page.locator('#cards button').click();
  await expect(page.locator('#title')).toHaveText('Открылась библиотека');
  await page.locator('#viewed').click();
  await expect(page.locator('#cards button')).toHaveCount(0);
  assert.deepEqual(await api('cards'),[]); assert.equal((await api('navigation')).count,0);
  assert.deepEqual(errors,[]);
 } finally {
  t.signal.removeEventListener('abort',abort);
  try{if(browser)await browser.close();}finally{
   try{
    if(child.exitCode===null&&child.signalCode===null){child.kill('SIGTERM'); const timer=setTimeout(()=>child.kill('SIGKILL'),3000); try{await exit;}finally{clearTimeout(timer);}}
   }finally{await fs.rm(data,{recursive:true,force:true});}
  }
 }
});
