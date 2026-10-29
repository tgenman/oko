import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader';
import {artifactPath} from './artifacts';

test('only login is visible before authentication on desktop and mobile; logout clears both tabs', {timeout:45000}, async t=>{
  const {page,context,origin,token,api}=await productionReader(t,{persistent:true});
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.stack??error.message));
  // Neither a deep link nor the old launcher fragment opens the reader.
  await page.goto(origin+'/material/private#token='+token);
  await expect(page).toHaveURL(new RegExp('^'+origin.replaceAll('.','\\.')+'/login#?$'));
  await expect(page.locator('#reader-shell')).toHaveCount(0);
  await expect(page.getByLabel('Логин')).toBeVisible();
  await expect(page.getByLabel('Пароль')).toBeVisible();
  await page.screenshot({path:artifactPath('password-login-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:artifactPath('password-login-mobile.png'),fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.getByLabel('Логин').fill('reader');
  await page.getByLabel('Пароль').fill('wrong-password');
  await page.getByRole('button',{name:'Войти'}).click();
  await expect(page.getByRole('alert')).toHaveText('Неверный логин или пароль.');
  await page.getByLabel('Логин').fill('reader');
  await page.getByLabel('Пароль').fill('synthetic-password');
  await page.getByRole('button',{name:'Войти'}).click();
  await page.setViewportSize({width:1440,height:1000});
  await expect(page.locator('#stop')).toBeEnabled();
  await expect(page.getByRole('button',{name:'Выйти'})).toBeVisible();
  const second=await context.newPage();
  await second.goto(origin+'/');
  await expect(second.locator('#stop')).toBeEnabled();
  await page.getByRole('button',{name:'Выйти'}).click();
  await expect(page.getByLabel('Логин')).toBeVisible();
  await expect(second.getByLabel('Логин')).toBeVisible();
  await second.goto(origin+'/docs');
  await second.goBack();
  await expect(second.getByLabel('Логин')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Логин')).toBeVisible();
  await expect(page.locator('#reader-shell')).toHaveCount(0);
  // Reader remains available to its internal administrative caller.
  expect((await api('status/details')).lifetime.kind).toBe('explicit');
  expect(errors).toEqual([]);
});

test('revoked cookie clears an idle reader and pending delayed replies', {timeout:45000}, async t=>{
  const {page,context,origin}=await productionReader(t,{persistent:true});
  await page.goto(origin+'/');
  await page.getByLabel('Логин').fill('reader');
  await page.getByLabel('Пароль').fill('synthetic-password');
  await page.getByRole('button',{name:'Войти'}).click();
  await expect(page.getByRole('button',{name:'Выйти'})).toBeVisible();
  const cookies=await context.cookies();
  expect(cookies.find(c=>c.name==='oko_access')?.httpOnly).toBe(true);
  // A second client revokes the credential without a BroadcastChannel notification.
  const response=await context.request.delete(origin+'/api/v3/browser-session',{headers:{Origin:origin}});
  expect(response.status()).toBe(200);
  await expect(page.getByLabel('Логин')).toBeVisible({timeout:10000});
  await expect(page.locator('#reader-shell')).toHaveCount(0);
});

test('logout aborts a response body that has already delivered its headers', {timeout:45000}, async t=>{
  const {page,origin}=await productionReader(t,{persistent:true});
  await page.goto(origin+'/');
  await page.getByLabel('Логин').fill('reader');
  await page.getByLabel('Пароль').fill('synthetic-password');
  await page.getByRole('button',{name:'Войти'}).click();
  await expect(page.getByRole('button',{name:'Выйти'})).toBeVisible();
  const ts=await import('typescript');
  const fs=await import('node:fs/promises');
  const source=await fs.readFile((await import('node:path')).resolve(__dirname,'../../frontend/src/access.ts'),'utf8');
  const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
  await page.route('**/assets/access-probe.js',route=>route.fulfill({contentType:'text/javascript',body:compiled}));
  const observed=new Set<string>();
  page.on('console',message=>observed.add(message.text()));
  await page.evaluate(async()=>{
    const moduleUrl='/assets/access-probe.js';
    const {createAccessGuard}=await import(moduleUrl);
    let finish:(()=>void)|undefined;
    const body=new ReadableStream<Uint8Array>({start(controller){finish=()=>{controller.enqueue(new TextEncoder().encode('late-private-body'));controller.close();};}});
    const guard=createAccessGuard(async(_input:RequestInfo|URL,options?:RequestInit)=>{
      options?.signal?.addEventListener('abort',()=>{console.log('probe-controller-aborted');finish?.();});
      return new Response(body,{headers:{'X-Oko-Password-Login':'1'}});
    });
    const response=await guard.fetch('/api/v3/status');
    void response.text().then(()=>console.log('probe-body-leaked'),()=>console.log('probe-body-rejected'));
    new BroadcastChannel('oko-access').postMessage('logout');
  });
  await expect(page.getByLabel('Логин')).toBeVisible();
  expect(observed.has('probe-controller-aborted')).toBe(true);
  expect(observed.has('probe-body-rejected')).toBe(true);
  expect(observed.has('probe-body-leaked')).toBe(false);
});

test('logout disposes layout observers before clearing the reader', {timeout:45000}, async t=>{
  const {page,origin}=await productionReader(t,{persistent:true});
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.stack??error.message));
  await page.goto(origin+'/');
  await page.getByLabel('Логин').fill('reader');
  await page.getByLabel('Пароль').fill('synthetic-password');
  await page.getByRole('button',{name:'Войти'}).click();
  await expect(page.locator('#stop')).toBeEnabled();
  await page.evaluate(()=>{
    const observer=new MutationObserver(()=>{
      if(!document.getElementById('reader-shell')){
        observer.disconnect();
        window.dispatchEvent(new Event('resize'));
      }
    });
    observer.observe(document.body,{childList:true});
  });
  await page.getByRole('button',{name:'Выйти'}).click();
  await page.getByLabel('Логин').waitFor();
  expect(errors).toEqual([]);
});
