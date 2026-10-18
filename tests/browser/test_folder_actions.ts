import type {ReaderFixture} from './support.ts';
import {test} from 'node:test';
import {reader, material, expect} from './support.ts';
import {artifactPath} from './artifacts.ts';

async function chooseFolder(r: ReaderFixture,id='f') {
  await r.page.locator('#folders [data-folder="'+id+'"]').click();
  await expect(r.page.locator('#folder-viewed')).toBeEnabled();
}
test('folder completion conflict keeps material and requires the refreshed token',async()=>{
  const r=await reader();try {
    await r.start([material('a')]);await chooseFolder(r);
    await r.page.locator('[data-card="a"]').click();
    await expect(r.page.locator('#text')).toContainText('Текст a');
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/v3/folders/f/completion/preview',{body:{cards:1,originals:1,token:'first'}});
      readerTransport.replies.set('/api/v3/folders/f/completion',{status:409,body:{detail:{message:'Состав папки изменился',preview:{cards:2,originals:2,token:'changed'}}}});
    });
    await r.page.locator('#folder-viewed').click();
    await expect(r.page.locator('#completion-confirm')).toBeEnabled();
    await r.page.locator('#completion-confirm').click();
    await expect(r.page.locator('#completion-message')).toHaveText('Состав папки изменился');
    await expect(r.page.locator('#completion-count')).toContainText('Cards: 2');
    await expect(r.page.locator('#text')).toContainText('Текст a');
    await r.page.screenshot({path:artifactPath('folder-completion-conflict.png')});
    await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/folders/f/completion',{body:{ok:true},next:[['/api/v3/cards',{body:[]}]]}));
    await r.page.locator('#completion-confirm').click();
    await expect(r.page.locator('#completion-dialog')).not.toBeVisible();
    await expect(r.page.locator('#cards button')).toHaveCount(0);
    await expect(r.page.locator('#text')).not.toContainText('Текст a');
    expect(await r.page.evaluate(()=>readerTransport.requests.filter(request=>request.url==='/api/v3/folders/f/completion').map(request=>request.body!.token))).toEqual(['first','changed']);
    expect(r.errors).toEqual([]);
  }finally{await r.close();}
});

test('late folder completion preserves another folder material and confirmation dialog',async()=>{
  const r=await reader();try {
    await r.start([material('a'),material('b',{feed_id:'other-feed',folder_id:'other'})]);
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/v3/reading-composition',{body:{count:2,folders:[{id:'f',title:'Тема',count:1},{id:'other',title:'Другая',count:1}],feeds:[{id:'feed',title:'feed',folder_id:'f',kind:'rss',count:1},{id:'other-feed',title:'Другой фид',folder_id:'other',kind:'rss',count:1}]}});
      readerTransport.replies.set('/api/v3/folders/f/completion/preview',{body:{cards:1,originals:1,token:'first'}});
      readerTransport.replies.set('/api/v3/folders/other/completion/preview',{body:{cards:1,originals:1,token:'other'}});
      readerTransport.held.set('/api/v3/folders/f/completion',true);
    });
    await expect(r.page.locator('#folders [data-folder="other"]')).toBeVisible();
    await chooseFolder(r);await r.page.locator('#folder-viewed').click();
    await expect(r.page.locator('#completion-confirm')).toBeEnabled();
    await r.page.locator('#completion-confirm').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/folders/f/completion'));
    await r.page.locator('#completion-close').click();await chooseFolder(r,'other');
    await r.page.locator('[data-card="b"]').click();
    await expect(r.page.locator('#text')).toContainText('Текст b');
    await r.page.locator('#folder-viewed').click();
    await expect(r.page.locator('#completion-count')).toContainText('Cards: 1');
    await r.page.evaluate(()=>readerTransport.release('/api/v3/folders/f/completion',{ok:true}));
    await r.page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await expect(r.page.locator('#completion-dialog')).toBeVisible();
    await expect(r.page.locator('#text')).toContainText('Текст b');
    await expect(r.page.locator('#completion-confirm')).toBeEnabled();
    expect(r.errors).toEqual([]);
  }finally{await r.close();}
});
