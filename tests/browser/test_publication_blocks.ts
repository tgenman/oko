import {test} from 'node:test';
import {reader,material,expect} from './support';

test('opened publication renders body from common content blocks',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{text:'Old projection',content:{blocks:[{type:'text',role:'body',text:'Body from content'}]}})]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text')).toContainText('Body from content');
    await expect(r.page.locator('#text')).not.toContainText('Old projection');
  }finally{await r.close();}
});

test('common blocks show quote, description, linked article and original media links',async()=>{
  const r=await reader();
  try{
    const url='/api/v3/materials/article/media/image';
    await r.start([material('A',{
      text:'Old projection',description:'',reply_preview:null,linked_content:null,
      content:{blocks:[
        {type:'quote',text:'Quoted words',author:'Author',source_url:'https://example.org/quoted'},
        {type:'text',role:'description',text:'Common description'},
        {type:'text',role:'body',text:'Root body'},
        {type:'publication',publication_id:'article',source_url:'https://example.org/article'}]},
      linked_publications:[{id:'article',title:'Article title',source_url:'https://example.org/article',status:'ready',content:{blocks:[
        {type:'text',role:'body',text:'Article body'},
        {type:'media',role:'image',media_id:'image',source_url:'https://cdn.example/image.png',publication_url:'https://example.org/article'}]}}],
      media:[{id:'image',name:'image.png',mime:'image/png',size_bytes:10,source_url:'https://cdn.example/image.png',url,status:'ready',reason_code:null,reason_message:null,kind:'image',role:'image'}]
    })]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#reply-preview')).toContainText('Quoted words');
    await expect(r.page.locator('#reply-preview a')).toHaveAttribute('href','https://example.org/quoted');
    await expect(r.page.locator('#description')).toContainText('Common description');
    await expect(r.page.locator('#text')).toContainText('Root body');
    await expect(r.page.locator('#text')).toContainText('Article title');
    await expect(r.page.locator('#text')).toContainText('Article body');
    await expect(r.page.locator('#text a[href="https://cdn.example/image.png"]')).toBeVisible();
  }finally{await r.close();}
});

test('media-only publication shows links to the file and its source',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{text:'',content:{blocks:[{type:'media',role:'image',media_id:'image',source_url:'https://cdn.example/image.png',publication_url:'https://example.org/post'}]},
      media:[{id:'image',name:'image.png',mime:'image/png',size_bytes:10,source_url:'https://cdn.example/image.png',url:'/api/v3/materials/A/media/image',status:'ready',reason_code:null,reason_message:null,kind:'image',role:'image'}]})]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text a[href="https://cdn.example/image.png"]')).toBeVisible();
    await expect(r.page.locator('#text a[href="https://example.org/post"]')).toBeVisible();
  }finally{await r.close();}
});

test('subtitle media bytes are shown as publication text',async()=>{
  const r=await reader();
  try{
    const url='/api/v3/materials/A/media/subtitles';
    await r.start([material('A',{text:'Old projection',content:{blocks:[{type:'media',role:'subtitles',media_id:'subtitles',source_url:null,publication_url:'https://example.org/video'}]},
      media:[{id:'subtitles',name:'captions.txt',mime:'text/plain',size_bytes:13,source_url:null,url,status:'ready',reason_code:null,reason_message:null,kind:'file',role:'attachment'}]})]);
    await r.page.evaluate(url=>readerTransport.replies.set(url,{base64:btoa('Caption words'),mime:'text/plain'}),url);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text')).toContainText('Caption words');
    await expect(r.page.locator('#text')).not.toContainText('Old projection');
  }finally{await r.close();}
});

test('text and media follow their common block order',async()=>{
  const r=await reader();
  try{
    const url='/api/v3/materials/A/media/image';
    await r.start([material('A',{content:{blocks:[
      {type:'text',role:'body',text:'Before image'},
      {type:'media',role:'image',media_id:'image',source_url:'https://cdn.example/image.png',publication_url:'https://example.org/post'},
      {type:'text',role:'body',text:'After image'}]},
      media:[{id:'image',name:'image.png',mime:'image/png',size_bytes:68,source_url:'https://cdn.example/image.png',url,status:'ready',reason_code:null,reason_message:null,kind:'image',role:'image'}]})]);
    await r.page.evaluate(url=>readerTransport.replies.set(url,{base64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1sAAAAASUVORK5CYII=',mime:'image/png'}),url);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text img')).toBeVisible();
    const ordered=await r.page.locator('#text').evaluate(root=>{
      const before=[...root.querySelectorAll('p')].find(node=>node.textContent==='Before image');
      const image=root.querySelector('img');
      const after=[...root.querySelectorAll('p')].find(node=>node.textContent==='After image');
      return !!before&&!!image&&!!after&&!!(before.compareDocumentPosition(image)&Node.DOCUMENT_POSITION_FOLLOWING)&&!!(image.compareDocumentPosition(after)&Node.DOCUMENT_POSITION_FOLLOWING);
    });
    expect(ordered).toBe(true);
  }finally{await r.close();}
});

test('external media uses its common block without platform-specific data',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{external_attachments:[],content:{blocks:[
      {type:'text',role:'body',text:'Post body'},
      {type:'external_media',kind:'audio',url:'https://example.org/audio',title:'A recorded lesson',author:'Author'}]}})]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text')).toContainText('A recorded lesson');
    await expect(r.page.locator('#text')).toContainText('Author');
    await expect(r.page.locator('#text a[href="https://example.org/audio"]')).toBeVisible();
    await expect(r.page.locator('#text')).not.toContainText('Telegram');
  }finally{await r.close();}
});

test('quote stays at its position between text blocks',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{reply_preview:null,content:{blocks:[
      {type:'text',role:'body',text:'Before quote'},
      {type:'quote',text:'Quoted words',author:'Author',source_url:'https://example.org/quoted'},
      {type:'text',role:'body',text:'After quote'}]}})]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text #reply-preview')).toContainText('Quoted words');
    const ordered=await r.page.locator('#text').evaluate(root=>{
      const before=[...root.querySelectorAll('p')].find(node=>node.textContent==='Before quote');
      const quote=root.querySelector('#reply-preview');
      const after=[...root.querySelectorAll('p')].find(node=>node.textContent==='After quote');
      return !!before&&!!quote&&!!after&&!!(before.compareDocumentPosition(quote)&Node.DOCUMENT_POSITION_FOLLOWING)&&!!(quote.compareDocumentPosition(after)&Node.DOCUMENT_POSITION_FOLLOWING);
    });
    expect(ordered).toBe(true);
  }finally{await r.close();}
});

test('unavailable publication shows its job reason and original link without platform fields',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{source_url:'https://example.org/original',text:'',content:{blocks:[]},status:'pending_collection',
      jobs:[{operation:'fetch',object_id:'A',status:'unavailable',reason_code:'access',reason_message:'Source is unavailable',retry_at:null}]})]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#source-unavailable')).toContainText('Source is unavailable');
    await expect(r.page.locator('#source-unavailable a')).toHaveAttribute('href','https://example.org/original');
  }finally{await r.close();}
});

test('available fragment stays readable while publication job is unavailable',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{availability:'pending',status:'pending_collection',
      content:{blocks:[{type:'text',role:'body',text:'Available fragment'}]},
      jobs:[{operation:'fetch',object_id:'A',status:'unavailable',reason_code:'access',reason_message:'Source is unavailable',retry_at:null}]})]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text')).toContainText('Available fragment');
    await expect(r.page.locator('#source-unavailable')).toContainText('Source is unavailable');
  }finally{await r.close();}
});

test('accepted summary remains visible while its new job runs',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{theses:{state:'disabled',result:null,error:null},
      summarized_content:{points:[{text:'Accepted summary',source_publication_ids:['A']}]},
      processing_metadata:{summary:{processor:'local',model:'example',completed_at:10}},
      jobs:[{operation:'theses',object_id:'A',status:'running',reason_code:null,reason_message:null,retry_at:null}]})]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#theses')).toContainText('Accepted summary');
    await expect(r.page.locator('#ai-status')).toContainText('Generating summary');
  }finally{await r.close();}
});

test('filtered text comes from filtered blocks rather than a display projection',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{
      text:'Original body',content:{blocks:[{type:'text',role:'body',text:'Original body'}]},
      filter:{state:'ready',error:null,result:{hidden:false,reason:'Removed',removed:['ad'],text:'Old projection',provenance:{kind:'unknown',reason:'not_recorded'}}},
      filtered_content:{hidden:false,reason:'Removed',removed:[{publication_id:'A',block_index:0,text:'ad'}],blocks:[{type:'text',role:'body',text:'Filtered body'}]}
    })]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#filtered-text')).toContainText('Filtered body');
    await expect(r.page.locator('#filtered-text')).not.toContainText('Old projection');
  }finally{await r.close();}
});

test('linked fetch failure stays with the linked publication',async()=>{
  const r=await reader();
  try{
    await r.start([material('A',{source_url:'https://example.org/root',content:{blocks:[{type:'text',role:'body',text:'Root body'},{type:'publication',publication_id:'article',source_url:'https://example.org/article'}]},
      linked_publications:[{id:'article',title:'Article',source_url:'https://example.org/article',status:'pending_collection',content:{blocks:[]}}],
      jobs:[{operation:'fetch',object_id:'article',status:'unavailable',reason_code:'access',reason_message:'Article unavailable',retry_at:null}]})]);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#source-unavailable')).toHaveCount(0);
    await expect(r.page.locator('[aria-label="Linked publication"]')).toContainText('Article unavailable');
    await expect(r.page.locator('[aria-label="Linked publication"] a[href="https://example.org/article"]')).toBeVisible();
  }finally{await r.close();}
});

test('switching revision resets quote expansion and old subtitle text',async()=>{
  const r=await reader();
  try{
    const oldUrl='/api/v3/materials/A/media/old',newUrl='/api/v3/materials/A/media/new';
    const old=material('A',{content:{blocks:[{type:'quote',text:'Old quote',author:'A',source_url:null},{type:'media',role:'subtitles',media_id:'old',source_url:null,publication_url:null}]},media:[{id:'old',name:'old',mime:'text/plain',size_bytes:3,source_url:null,url:oldUrl,status:'ready',reason_code:null,reason_message:null,kind:'file',role:'attachment'}]});
    await r.start([old]);
    await r.page.evaluate(({oldUrl,newUrl})=>{readerTransport.replies.set(oldUrl,{base64:btoa('Old subtitles'),mime:'text/plain'});readerTransport.held.set(newUrl,true);},{oldUrl,newUrl});
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text')).toContainText('Old subtitles');
    await r.page.locator('#reply-preview button').click();
    const fresh={...old,content:{blocks:[{type:'quote' as const,text:'New quote',author:'B',source_url:null},{type:'media' as const,role:'subtitles' as const,media_id:'new',source_url:null,publication_url:null}]},media:[{...old.media[0],id:'new',url:newUrl}]};
    await r.page.evaluate(fresh=>readerTransport.replies.set('/api/v3/publication/A',{body:fresh}),fresh);
    await r.page.locator('#material-update-button').click();
    await expect(r.page.locator('#text')).toContainText('New quote');
    await expect(r.page.locator('#text')).not.toContainText('Old subtitles');
    await expect(r.page.locator('#reply-preview button')).toHaveAttribute('aria-expanded','false');
  }finally{await r.close();}
});

test('subtitle disappears when its file becomes unavailable without a summary',async()=>{
  const r=await reader();
  try{
    const url='/api/v3/materials/A/media/captions';
    const old=material('A',{summarized_content:{},content:{blocks:[{type:'media',role:'subtitles',media_id:'captions',source_url:null,publication_url:null}]},media:[{id:'captions',name:'captions',mime:'text/plain',size_bytes:3,source_url:null,url,status:'ready',reason_code:null,reason_message:null,kind:'file',role:'attachment'}]});
    await r.start([old]);
    await r.page.evaluate(url=>readerTransport.replies.set(url,{base64:btoa('Old subtitles'),mime:'text/plain'}),url);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text')).toContainText('Old subtitles');
    const fresh={...old,media:[{...old.media[0],url:null,status:'unavailable' as const}]};
    await r.page.evaluate(fresh=>readerTransport.replies.set('/api/v3/publication/A',{body:fresh}),fresh);
    await expect(r.page.locator('#text')).not.toContainText('Old subtitles');
  }finally{await r.close();}
});

test('switching revision preserves an unchanged media element',async()=>{
  const r=await reader();
  try{
    const url='/api/v3/materials/A/media/video';
    const old=material('A',{content:{blocks:[{type:'media',role:'video',media_id:'video',source_url:'https://cdn.example/v.mp4',publication_url:null},{type:'text',role:'body',text:'Old body'}]},media:[{id:'video',name:'video',mime:'video/mp4',size_bytes:3,source_url:'https://cdn.example/v.mp4',url,status:'ready',reason_code:null,reason_message:null,kind:'video',role:'attachment'}]});
    await r.start([old]);
    await r.page.evaluate(url=>readerTransport.replies.set(url,{base64:btoa('video'),mime:'video/mp4'}),url);
    await r.page.locator('[data-card="A"]').click();
    await expect(r.page.locator('#text video')).toBeVisible();
    await r.page.locator('#text video').evaluate(element=>element.setAttribute('data-test-retained','yes'));
    const fresh={...old,content:{blocks:[{type:'media' as const,role:'image' as const,media_id:'new-image',source_url:null,publication_url:null},old.content.blocks![0],{type:'text' as const,role:'body' as const,text:'New body'}]}};
    await r.page.evaluate(fresh=>readerTransport.replies.set('/api/v3/publication/A',{body:fresh}),fresh);
    await r.page.locator('#material-update-button').click();
    await expect(r.page.locator('#text')).toContainText('New body');
    await expect(r.page.locator('#text video')).toHaveAttribute('data-test-retained','yes');
  }finally{await r.close();}
});
