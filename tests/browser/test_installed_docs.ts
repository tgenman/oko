import {test} from 'node:test';
import {spawn, execFileSync} from 'node:child_process';
import {mkdtemp, readdir, rm, mkdir, copyFile, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {newContext} from './browser-context.ts';
import {artifactPath} from './artifacts.ts';
import assert from 'node:assert/strict';
import * as path from 'node:path';

test('installed reader and Swagger use local packaged resources, explicit bearer and memory-only auth', async () => {
  const root = path.resolve(__dirname,'../..');
  const python = process.env.OKO_PYTHON || path.join(root,'.venv/bin/python');
  const directory = await mkdtemp(path.join(tmpdir(),'oko-installed-browser-'));
  const wheels = path.join(directory,'wheels'), installed = path.join(directory,'installed');
  execFileSync(python,[path.join(root,'scripts/build-package.py'),'--output',wheels],{cwd:root});
  const wheel = (await readdir(wheels)).find(name=>name.endsWith('.whl'));
  execFileSync(python,[path.join(root,'tests/support/install_wheel.py'),path.join(wheels,wheel!),installed]);
  await mkdir(path.join(directory,'tests/support'),{recursive:true});
  for(const name of ['app.py','installed_reader.py']) await copyFile(path.join(root,'tests/support',name),path.join(directory,'tests/support',name));
  for(const name of ['tests/__init__.py','tests/support/__init__.py']) await writeFile(path.join(directory,name),'');
  const server = spawn(python,['-m','tests.support.installed_reader',path.join(directory,'data'),'synthetic'],
    {cwd:directory,env:{PYTHONPATH:installed+path.delimiter+directory,PATH:''},stdio:['ignore','pipe','pipe']});
  let output='', errors='';
  server.stderr.on('data',data=>errors+=data);
  const origin = await new Promise((resolve,reject)=>{
    server.stdout.on('data',data=>{output+=data;const line=output.split('\n').find(line=>line.startsWith('{'));if(line)resolve(JSON.parse(line).origin);});
    server.once('exit',code=>reject(new Error(`Installed fixture exited ${code}: ${errors}`)));
  });
  const context = await newContext();
  try {
    for(let attempt=0;attempt<100;attempt++) {
      try {if((await fetch(origin+'/openapi.json')).ok)break;} catch { /* starting */ }
      await new Promise(resolve=>setTimeout(resolve,20));
    }
    const page = await context.newPage(), requests: string[] = [];
    page.on('request',request=>requests.push(request.url()));
    await page.goto(origin+'/docs');
    await page.locator('.opblock').last().waitFor();
    assert.equal(await page.locator('.opblock').count(),50);
    assert(!requests.some(url=>new URL(url).pathname.startsWith('/api/')));
    assert(requests.every(url=>url.startsWith(origin+'/')));
    let status=await(await fetch(origin+'/api/status',{headers:{Authorization:'Bearer synthetic'}})).json();
    assert.equal(status.live_tabs,0);
    await page.locator('.opblock-summary').filter({hasText:'/api/status'}).click();
    await page.getByRole('button',{name:'Try it out',exact:true}).click();
    const unauth=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/status');
    await page.getByRole('button',{name:'Execute',exact:true}).click();
    assert.equal((await unauth).status(),403);
    await page.getByRole('button',{name:'Authorize',exact:true}).first().click();
    await page.locator('.dialog-ux input').fill('synthetic');
    await page.locator('.dialog-ux').getByRole('button',{name:'Apply credentials',exact:true}).click();
    await page.locator('.dialog-ux').getByRole('button',{name:'Close',exact:true}).click();
    const authorized=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/status');
    await page.getByRole('button',{name:'Execute',exact:true}).click();
    assert.equal((await authorized).status(),200);
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    await page.screenshot({path:artifactPath('installed-swagger-authorized.png'),fullPage:true});
    await page.goto(origin+'/docs');
    await page.locator('.opblock-summary').filter({hasText:'/api/status'}).click();
    await page.getByRole('button',{name:'Try it out',exact:true}).click();
    const forgotten=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/status');
    await page.getByRole('button',{name:'Execute',exact:true}).click();
    assert.equal((await forgotten).status(),403);
    status=await(await fetch(origin+'/api/status',{headers:{Authorization:'Bearer synthetic'}})).json();
    assert.equal(status.live_tabs,0);
    await page.goto(origin+'/#token=synthetic');
    await page.locator('#collect').waitFor({state:'visible'});
    assert.equal(await page.locator('.brand').innerText(),'Око');
    await page.screenshot({path:artifactPath('installed-reader.png'),fullPage:true});
  } finally {
    await context.close();server.kill('SIGTERM');
    await new Promise(resolve=>server.once('exit',resolve));
    await rm(directory,{recursive:true,force:true});
  }
});
