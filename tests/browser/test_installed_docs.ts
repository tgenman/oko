import {test} from 'node:test';
import {productionReader} from './http-reader.ts';
import {artifactPath} from './artifacts.ts';
import assert from 'node:assert/strict';

test('reader Swagger uses local resources, explicit bearer and memory-only auth', async () => {
  const fixture = await productionReader();
  const {origin, context, token} = fixture;
  try {
    const page = await context.newPage(), requests: string[] = [];
    page.on('request',request=>requests.push(request.url()));
    await page.goto(origin+'/docs');
    await page.locator('.opblock').last().waitFor();
    assert.equal(await page.locator('.opblock').count(),50);
    assert(!requests.some(url=>new URL(url).pathname.startsWith('/api/')));
    assert(requests.every(url=>url.startsWith(origin+'/')));
    let status=await(await fetch(origin+'/api/v3/status',{headers:{Authorization:'Bearer ' + token}})).json();
    assert.equal(status.active_client_sessions,0);
    await page.locator('.opblock-summary').filter({hasText:'/api/v3/status'}).click();
    await page.getByRole('button',{name:'Try it out',exact:true}).click();
    const unauth=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v3/status');
    await page.getByRole('button',{name:'Execute',exact:true}).click();
    assert.equal((await unauth).status(),403);
    await page.getByRole('button',{name:'Authorize',exact:true}).first().click();
    await page.locator('.dialog-ux input').fill(token);
    await page.locator('.dialog-ux').getByRole('button',{name:'Apply credentials',exact:true}).click();
    await page.locator('.dialog-ux').getByRole('button',{name:'Close',exact:true}).click();
    const authorized=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v3/status');
    await page.getByRole('button',{name:'Execute',exact:true}).click();
    assert.equal((await authorized).status(),200);
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    await page.screenshot({path:artifactPath('installed-swagger-authorized.png'),fullPage:true});
    await page.goto(origin+'/docs');
    await page.locator('.opblock-summary').filter({hasText:'/api/v3/status'}).click();
    await page.getByRole('button',{name:'Try it out',exact:true}).click();
    const forgotten=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v3/status');
    await page.getByRole('button',{name:'Execute',exact:true}).click();
    assert.equal((await forgotten).status(),403);
    status=await(await fetch(origin+'/api/v3/status',{headers:{Authorization:'Bearer ' + token}})).json();
    assert.equal(status.active_client_sessions,0);
    await page.goto(origin+'/#token='+token);
    await page.locator('#collect').waitFor({state:'visible'});
    assert.equal(await page.locator('.brand').innerText(),'Око');
    await page.screenshot({path:artifactPath('installed-reader.png'),fullPage:true});
  } finally {
    await fixture.close();
  }
});
