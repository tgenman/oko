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
