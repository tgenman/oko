import type {ReaderFixture} from './support.ts';
import {test} from 'node:test';
import {reader, material, expect} from './support.ts';
import {artifactPath} from './artifacts.ts';

async function chooseFolder(r: ReaderFixture,id='f') {
  await r.page.locator('#folders [data-folder="'+id+'"]').click();
  await expect(r.page.locator('#folder-viewed')).toBeEnabled();
}

test('late folder completion preserves another folder material and confirmation dialog',async()=>{
  const r=await reader();try {
    await r.start([material('a'),material('b',{feed_id:'other-feed',folder_id:'other'})]);
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/v3/navigation',{body:{total:2,folders:[{id:'f',title:'Тема',count:1,feeds:[window.readerFixtures.navigationFeed({id:'feed',title:'feed',count:1})]},{id:'other',title:'Другая',count:1,feeds:[window.readerFixtures.navigationFeed({id:'other-feed',title:'Другой фид',count:1})]}]}});
      const cards=readerTransport.replies.get('/api/v3/cards')!.body!.items;
      readerTransport.replies.set('/api/v3/cards?sort=newest&folder=f',{body:window.readerFixtures.cardPage(cards.filter(c=>c.feed_id==='feed'))});
      readerTransport.replies.set('/api/v3/cards?sort=newest&folder=other',{body:window.readerFixtures.cardPage(cards.filter(c=>c.feed_id==='other-feed'))});
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

test('reopening a folder confirmation cannot duplicate its pending completion',async()=>{
  const r=await reader();try {
    await r.start([material('a')]);await chooseFolder(r);
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/v3/folders/f/completion/preview',{body:{token:'captured',cards_count:1,publications_count:1}});
      readerTransport.held.set('/api/v3/folders/f/completion',true);
    });
    await r.page.locator('#folder-viewed').click();await expect(r.page.locator('#completion-confirm')).toBeEnabled();
    await r.page.locator('#completion-confirm').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/folders/f/completion'));
    await r.page.locator('#completion-close').click();await r.page.locator('#folder-viewed').click();
    await expect(r.page.locator('#completion-count')).toContainText('Cards: 1');
    await expect(r.page.locator('#completion-confirm')).toBeDisabled();
    expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>q.url==='/api/v3/folders/f/completion').length)).toBe(1);
    await r.page.evaluate(()=>readerTransport.release('/api/v3/folders/f/completion',{ok:true}));
    await expect(r.page.locator('#completion-confirm')).toBeEnabled();
    await expect(r.page.locator('#completion-dialog')).toBeVisible();
    expect(r.errors).toEqual([]);
  }finally{await r.close();}
});
