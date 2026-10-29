import {test} from 'node:test';
import {reader,material,expect,type ReaderFixture} from './support';
import {artifactPath} from './artifacts';

type Scope='feed'|'folder';
function controls(r:ReaderFixture,scope:Scope){return {
  confirm:r.page.locator(scope==='feed'?'#feed-action-confirm':'#completion-confirm'),
  cancel:r.page.locator(scope==='feed'?'#feed-action-cancel':'#completion-close'),
  dialog:r.page.locator(scope==='feed'?'#feed-action-dialog':'#completion-dialog'),
  count:r.page.locator(scope==='feed'?'#feed-completion-count':'#completion-count'),
};}
function path(scope:Scope,id=scope==='feed'?'feed':'f'){return '/api/v3/'+(scope==='feed'?'feeds':'folders')+'/'+id+'/completion';}
async function open(r:ReaderFixture,scope:Scope,id=scope==='feed'?'feed':'f'){
  if(scope==='feed'){
    await r.page.locator('[data-feed="'+id+'"]').click({button:'right'});
    await r.page.getByRole('menuitem',{name:'Mark All as Read',exact:true}).click();
  }else{
    await r.page.locator('#folders [data-folder="'+id+'"]').click();
    await r.page.locator('#folder-viewed').click();
  }
}
async function release(r:ReaderFixture,scope:Scope,status=200){
  await r.page.evaluate(async({scope,url,status})=>{
    const channel=new BroadcastChannel('oko-reader');
    try{await new Promise<void>((resolve,reject)=>{
      const timer=window.setTimeout(()=>reject(new Error('Completion result was not reconciled')),5000);
      channel.onmessage=event=>{if(event.data?.kind===scope+'-viewed'){window.clearTimeout(timer);resolve();}};
      readerTransport.release(url,status===200?{ok:true}:{code:'storage_unavailable',message:'Cleanup not finished',details:{},command_id:null,diagnostic_id:null},status);
    });}finally{channel.close();}
  },{scope,url:path(scope),status});
}

for(const scope of ['folder','feed'] as const){
  test(scope+' completion conflict replaces the captured token without replay',async()=>{
    const r=await reader();try{
      await r.start([material('a')]);const url=path(scope),c=controls(r,scope);
      await r.page.evaluate(url=>{readerTransport.replies.set(url+'/preview',{body:{token:'first',cards_count:1,publications_count:1}});readerTransport.replies.set(url,{status:409,body:{code:'composition_changed',message:'Composition changed',details:{preview:{token:'next',cards_count:2,publications_count:2}},command_id:null,diagnostic_id:null}});},url);
      await open(r,scope);await expect(c.confirm).toBeEnabled();
      expect(await r.page.evaluate(url=>readerTransport.requests.filter(q=>q.url===url).length,url)).toBe(0);
      await c.confirm.click();await expect(c.dialog).toContainText('Composition changed');await expect(c.count).toContainText(scope==='feed'?'Items: 2':'Cards: 2');
      await expect(c.confirm).toBeEnabled();
      expect(await r.page.evaluate(url=>readerTransport.requests.filter(q=>q.url===url).map(q=>q.body!.token),url)).toEqual(['first']);
      await r.page.screenshot({path:artifactPath(scope+'-completion.png')});
      await r.page.evaluate(url=>readerTransport.replies.set(url,{body:{ok:true}}),url);await c.confirm.click();await expect(c.dialog).not.toBeVisible();
      expect(await r.page.evaluate(url=>readerTransport.requests.filter(q=>q.url===url).map(q=>q.body!.token),url)).toEqual(['first','next']);expect(r.errors).toEqual([]);
    }finally{await r.close();}
  });

  for(const status of (scope==='feed'?[503]:[200,503]))test(scope+' completion '+status+' preserves new material after the captured items were accepted',async()=>{
    const r=await reader();try{
      await r.start([material('old')]);const url=path(scope),c=controls(r,scope);
      await r.page.evaluate(url=>{readerTransport.replies.set(url+'/preview',{body:{token:'captured',cards_count:1,publications_count:1}});readerTransport.held.set(url,true);},url);
      await open(r,scope);await expect(c.confirm).toBeEnabled();await c.confirm.click();await r.page.waitForFunction(url=>readerTransport.pending.has(url),url);await c.cancel.click();
      const fresh=material('fresh');await r.page.evaluate(fresh=>{readerTransport.replies.set('/api/v3/cards',{body:[fresh]});readerTransport.replies.set('/fixture/material/fresh',{body:fresh});},fresh);
      await expect(r.page.locator('[data-card="fresh"]')).toBeVisible();await r.page.locator('[data-card="fresh"]').click();await expect(r.page.locator('#text')).toContainText('Текст fresh');
      await release(r,scope,status);await expect(r.page.locator('#text')).toContainText('Текст fresh');await expect(r.page.locator('[data-card="fresh"]')).toBeVisible();expect(r.errors).toEqual([]);
    }finally{await r.close();}
  });

  test(scope+' notification reconciles removed cards without closing a newer publication',async()=>{
    const r=await reader();try{
      const fresh=material('fresh');await r.start([material('old'),fresh]);await r.page.locator('[data-card="fresh"]').click();await expect(r.page.locator('#text')).toContainText('Текст fresh');
      await r.page.evaluate(({fresh,scope})=>{readerTransport.replies.set('/api/v3/cards',{body:[fresh]});const channel=new BroadcastChannel('oko-reader');channel.postMessage(scope==='feed'?{kind:'feed-viewed',feed:'feed'}:{kind:'folder-viewed',folder:'f'});channel.close();},{fresh,scope});
      await expect(r.page.locator('[data-card="old"]')).toHaveCount(0);await expect(r.page.locator('#text')).toContainText('Текст fresh');
      await r.page.evaluate(scope=>{readerTransport.replies.set('/api/v3/cards',{body:[]});const channel=new BroadcastChannel('oko-reader');channel.postMessage(scope==='feed'?{kind:'feed-viewed',feed:'feed'}:{kind:'folder-viewed',folder:'f'});channel.close();},scope);
      await expect(r.page.locator('#cards button')).toHaveCount(0);await expect(r.page.locator('#text')).toContainText('Текст fresh');expect(r.errors).toEqual([]);
    }finally{await r.close();}
  });
}

test('unknown folder completion cannot reuse its old token and remains visible in Status',async()=>{
  const r=await reader();try{
    await r.start([material('a')]);const url=path('folder'),c=controls(r,'folder');
    await r.page.evaluate(url=>{readerTransport.replies.set(url+'/preview',{body:{token:'first',cards_count:1,publications_count:1}});readerTransport.held.set(url,true);},url);
    await open(r,'folder');await expect(c.confirm).toBeEnabled();await c.confirm.click();await r.page.waitForFunction(url=>readerTransport.pending.has(url),url);
    await r.page.evaluate(url=>{readerTransport.pending.get(url)!(Promise.reject(new TypeError('Connection lost')));readerTransport.pending.delete(url);readerTransport.held.delete(url);},url);
    await expect(c.dialog).toContainText('Action outcome unknown');await expect(c.confirm).toBeDisabled();await c.cancel.click();
    await r.page.locator('#status-toggle').click();await expect(r.page.locator('#status-panel')).toContainText('Action outcome unknown');await r.page.keyboard.press('Escape');
    await r.page.evaluate(url=>readerTransport.replies.set(url+'/preview',{body:{token:'fresh',cards_count:2,publications_count:2}}),url);
    await open(r,'folder');await expect(c.count).toContainText('Cards: 2');await expect(c.confirm).toBeEnabled();
    expect(await r.page.evaluate(url=>readerTransport.requests.filter(q=>q.url===url).map(q=>q.body!.token),url)).toEqual(['first']);
  }finally{await r.close();}
});

for(const scope of ['folder','feed'] as const)test(scope+' late failure cannot replace a newer confirmation that is still loading',async()=>{
  const r=await reader();try{
    await r.start([material('a'),material('b',{feed_id:'second',folder_id:'other'})]);const url=path(scope),next=path(scope,scope==='feed'?'second':'other'),c=controls(r,scope);
    await r.page.evaluate(({url,next})=>{
      const t=readerTransport;t.replies.set('/api/v3/reading-composition',{body:{count:2,folders:[{id:'f',title:'First',count:1},{id:'other',title:'Second',count:1}],feeds:[{id:'feed',title:'First',folder_id:'f',kind:'rss',count:1},{id:'second',title:'Second',folder_id:'other',kind:'rss',count:1}]}});
      t.replies.set(url+'/preview',{body:{token:'first',cards_count:1,publications_count:1}});t.held.set(url,true);t.held.set(next+'/preview',true);
    },{url,next});
    await expect(r.page.locator('[data-feed="second"]')).toBeVisible();await open(r,scope);await expect(c.confirm).toBeEnabled();await c.confirm.click();await r.page.waitForFunction(url=>readerTransport.pending.has(url),url);await c.cancel.click();
    await open(r,scope,scope==='feed'?'second':'other');await r.page.waitForFunction(url=>readerTransport.pending.has(url),next+'/preview');
    await release(r,scope,503);await expect(c.dialog).toBeVisible();await expect(c.confirm).toBeDisabled();await expect(c.dialog).not.toContainText('Cleanup not finished');
    await r.page.evaluate(next=>readerTransport.release(next+'/preview',{token:'second',cards_count:2,publications_count:2}),next);
    await expect(c.count).toContainText(scope==='feed'?'Items: 2':'Cards: 2');await expect(c.confirm).toBeEnabled();await expect(c.dialog).not.toContainText('Cleanup not finished');expect(r.errors).toEqual([]);
  }finally{await r.close();}
});
