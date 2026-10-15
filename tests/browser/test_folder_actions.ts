import type {ReaderFixture} from './support.ts';
import {test} from 'node:test';
import {reader, material, expect} from './support.ts';
import {artifactPath} from './artifacts.ts';

async function chooseFolder(r: ReaderFixture,id='f') {
  await r.page.locator('#folders [data-folder="'+id+'"]').click();
  await expect(r.page.locator('#folder-viewed')).toBeEnabled();
}
async function digest(r: ReaderFixture) {
  await r.page.locator('#list-menu-toggle').click();
  await r.page.locator('#digest-now').click();
  await expect(r.page.locator('#digest-dialog')).toBeVisible();
}

test('digest commands keep period mode and send selected dates as timestamps',async()=>{
  const r=await reader();try {
    await r.start([material('a')]);await chooseFolder(r);
    await r.page.evaluate(()=>readerTransport.replies.set('/api/digests/folder/f',{body:{id:'created'}}));
    await digest(r);
    await r.page.locator('#digest-start').fill('2026-10-01T12:00');
    await r.page.locator('#digest-end').fill('2026-10-02T12:00');
    const expected=await r.page.evaluate(()=>({period:true,start:new Date('2026-10-01T12:00').getTime()/1000,end:new Date('2026-10-02T12:00').getTime()/1000}));
    await r.page.locator('#digest-run').click();
    await expect(r.page.locator('#digest-dialog')).not.toBeVisible();
    await r.page.locator('#list-menu-toggle').click();
    await r.page.locator('#compress').click();
    await expect(r.page.locator('#digest-heading')).toHaveText('Сжать историю папки');
    await expect(r.page.locator('#digest-start')).toHaveValue('');
    await r.page.locator('#digest-run').click();
    await expect(r.page.locator('#digest-dialog')).not.toBeVisible();
    expect(await r.page.evaluate(()=>readerTransport.requests.filter(request=>request.url==='/api/digests/folder/f').map(request=>request.body))).toEqual([expected,{period:false}]);
    expect(r.errors).toEqual([]);
  }finally{await r.close();}
});

test('late digest success cannot close another folder dialog or replace its date draft',async()=>{
  const r=await reader();try {
    await r.start([material('a')]);
    await r.page.evaluate(()=>readerTransport.replies.set('/api/navigation',{body:{count:1,folders:[{id:'f',title:'Тема',count:1},{id:'other',title:'Другая',count:0}],feeds:[{id:'feed',title:'feed',folder_id:'f',kind:'rss',count:1}]}}));
    await expect(r.page.locator('#folders [data-folder="other"]')).toBeVisible();
    await chooseFolder(r);await digest(r);
    await r.page.evaluate(()=>readerTransport.held.set('/api/digests/folder/f',true));
    await r.page.locator('#digest-run').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/digests/folder/f'));
    await r.page.locator('#digest-close').click();
    await chooseFolder(r,'other');await digest(r);
    await r.page.locator('#digest-start').fill('2026-10-01T12:00');
    await r.page.evaluate(()=>readerTransport.release('/api/digests/folder/f',{id:'created'}));
    await r.page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await expect(r.page.locator('#digest-dialog')).toBeVisible();
    await expect(r.page.locator('#digest-start')).toHaveValue('2026-10-01T12:00');
  }finally{await r.close();}
});

test('folder completion conflict keeps material and requires the refreshed token',async()=>{
  const r=await reader();try {
    await r.start([material('a')]);await chooseFolder(r);
    await r.page.locator('[data-card="a"]').click();
    await expect(r.page.locator('#text')).toContainText('Текст a');
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/folders/f/completion/preview',{body:{cards:1,originals:1,token:'first'}});
      readerTransport.replies.set('/api/folders/f/completion',{status:409,body:{detail:{message:'Состав папки изменился',preview:{cards:2,originals:2,token:'changed'}}}});
    });
    await r.page.locator('#folder-viewed').click();
    await expect(r.page.locator('#completion-confirm')).toBeEnabled();
    await r.page.locator('#completion-confirm').click();
    await expect(r.page.locator('#completion-message')).toHaveText('Состав папки изменился');
    await expect(r.page.locator('#completion-count')).toContainText('Карточек: 2');
    await expect(r.page.locator('#text')).toContainText('Текст a');
    await r.page.screenshot({path:artifactPath('folder-completion-conflict.png')});
    await r.page.evaluate(()=>readerTransport.replies.set('/api/folders/f/completion',{body:{ok:true},next:[['/api/cards',{body:[]}]]}));
    await r.page.locator('#completion-confirm').click();
    await expect(r.page.locator('#completion-dialog')).not.toBeVisible();
    await expect(r.page.locator('#cards button')).toHaveCount(0);
    await expect(r.page.locator('#text')).not.toContainText('Текст a');
    expect(await r.page.evaluate(()=>readerTransport.requests.filter(request=>request.url==='/api/folders/f/completion').map(request=>request.body!.token))).toEqual(['first','changed']);
    expect(r.errors).toEqual([]);
  }finally{await r.close();}
});

test('late folder completion preserves another folder material and confirmation dialog',async()=>{
  const r=await reader();try {
    await r.start([material('a'),material('b',{folder_id:'other'})]);
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/navigation',{body:{count:2,folders:[{id:'f',title:'Тема',count:1},{id:'other',title:'Другая',count:1}],feeds:[{id:'feed',title:'feed',folder_id:'f',kind:'rss',count:2}]}});
      readerTransport.replies.set('/api/folders/f/completion/preview',{body:{cards:1,originals:1,token:'first'}});
      readerTransport.replies.set('/api/folders/other/completion/preview',{body:{cards:1,originals:1,token:'other'}});
      readerTransport.held.set('/api/folders/f/completion',true);
    });
    await expect(r.page.locator('#folders [data-folder="other"]')).toBeVisible();
    await chooseFolder(r);await r.page.locator('#folder-viewed').click();
    await expect(r.page.locator('#completion-confirm')).toBeEnabled();
    await r.page.locator('#completion-confirm').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/folders/f/completion'));
    await r.page.locator('#completion-close').click();await chooseFolder(r,'other');
    await r.page.locator('[data-card="b"]').click();
    await expect(r.page.locator('#text')).toContainText('Текст b');
    await r.page.locator('#folder-viewed').click();
    await expect(r.page.locator('#completion-count')).toContainText('Карточек: 1');
    await r.page.evaluate(()=>readerTransport.release('/api/folders/f/completion',{ok:true}));
    await r.page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await expect(r.page.locator('#completion-dialog')).toBeVisible();
    await expect(r.page.locator('#text')).toContainText('Текст b');
    await expect(r.page.locator('#completion-confirm')).toBeEnabled();
    expect(r.errors).toEqual([]);
  }finally{await r.close();}
});
