const {test}=require('node:test');
const fs=require('node:fs/promises');
const {reader,material,expect}=require('./support.cjs');
const {artifactPath}=require('./artifacts.cjs');
test('synthetic reader surfaces screenshots and geometry',async()=>{
 const measurements={};
 for(const width of [1440,390]){
  const r=await reader();try{
   await r.page.setViewportSize({width,height:900});
   const points={points:[{text:'Основной тезис синтетического материала',sources:[{id:'plain',url:'https://example.org/plain'}]}]};
   const source=material('source',{previous:[{url:'/api/previous'}]});
   await r.start([
    material('plain',{title:'We Finally Know More About Humanity’s First Civilization',text:'Сохранённый текст публикации.\n\n'+('Читаем без изменения вёрстки. '.repeat(20)),published:1801991220}),
    material('video',{title:'Видео с недоступными субтитрами',captions:'unavailable',full:false,reason:'YouTube ограничил получение субтитров',description:'Описание видео\n\n'+('Текст и длинная ссылка https://example.org/'+'example/'.repeat(12)+'\n').repeat(3),text:''}),
    material('theses',{theses:points,provenance:{processor:'codex',model:'synthetic',reasoning_effort:'high',fast:false}}),
    material('hidden',{filter:{status:'done',hidden:true,restored:false,reason:'Синтетический фильтр',removed:[]}}),
    material('digest',{sources:[source],digest:points}),
    material('media',{images:['/api/image'],previous:[{url:'/api/previous'}]}),
    material('pending'),material('failed')
   ]);
   await r.page.evaluate(()=>{
    const t=readerTransport;
    t.replies.set('/api/subscriptions',{body:{folders:[{id:'f',title:'Наука'}],feeds:[]}});
    t.replies.set('/api/telegram/accounts',{body:['synthetic']});
    t.replies.set('/api/telegram/topics',{body:[{id:1,title:'Общее'},{id:30,title:'Наука'}]});
    t.replies.set('/api/import/preview',{body:{folders:[{title:'Наука'}],feeds:[]}});
    t.replies.set('/api/feeds/verify',{body:{url:'https://example.org/feed.xml',title:'Синтетический источник',kind:'rss',verification:'verified'}});
    t.replies.set('/api/image',{mime:'image/png',base64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR9sAAAAASUVORK5CYII='});
    t.replies.set('/api/previous',{body:{title:'Предыдущая версия',text:'Ранее сохранённый текст',full:true,reason:'',images:[],attachments:[]}});
    t.held.set('/api/materials/pending',true);
    t.replies.set('/api/materials/failed',{status:503,body:{detail:'Синтетическая ошибка открытия'}});
   });
   async function shot(name,scope){
    await r.page.locator(scope).scrollIntoViewIfNeeded();
    // Rendering is stable after two frames; no arbitrary fixed sleeps.
    await r.page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const key=name+'-'+width;
    const geometry=()=>r.page.locator(scope).evaluate(n=>[n,...n.querySelectorAll('*')].filter(e=>e.getClientRects().length).map(e=>{
      const b=e.getBoundingClientRect(),s=getComputedStyle(e);return {tag:e.tagName,id:e.id,x:b.x,y:b.y,w:b.width,h:b.height,padding:s.padding,margin:s.margin,gap:s.gap,font:s.fontSize,weight:s.fontWeight,line:s.lineHeight,spacing:s.letterSpacing,display:s.display,overflow:s.overflow};
    }));
    measurements[key]=await geometry();
    const sheet=r.page.locator('link[href="/reader-surfaces.css"]');
    await sheet.evaluate(n=>{n.disabled=true;});
    const original=await geometry();
    await sheet.evaluate(n=>{n.disabled=false;});
    expect(measurements[key].length).toBe(original.length);
    for(let i=0;i<original.length;i++)for(const prop of Object.keys(original[i])){
      if(['x','y','w','h'].includes(prop))expect(Math.abs(measurements[key][i][prop]-original[i][prop]),key+' '+i+' '+prop).toBeLessThanOrEqual(1);
      else expect(measurements[key][i][prop],key+' '+i+' '+prop).toBe(original[i][prop]);
    }
    const image=artifactPath('surfaces-'+key+'.png');
    await r.page.screenshot({path:image});console.info('Surface screenshot:',image);
   }
   await shot('empty','main>article');
   await r.page.locator('#add-open').click();await expect(r.page.locator('#add-dialog')).toBeVisible();
   expect(await r.page.locator('#add-dialog').evaluate(n=>getComputedStyle(n).backgroundColor)).toBe('rgb(242, 242, 247)');
   await expect(r.page.locator('#rss-check')).toBeDisabled();
   await r.page.locator('#rss-check').hover();
   expect(await r.page.locator('#rss-check').evaluate(n=>getComputedStyle(n).backgroundColor)).toBe('rgb(229, 229, 234)');
   await shot('rss-empty','#add-dialog');
   await r.page.locator('#rss-url').fill('https://example.org/feed.xml');await r.page.locator('#rss-check').focus();await shot('rss-ready','#add-dialog');
   await r.page.locator('#rss-check').click();await expect(r.page.locator('#rss-checked-fields')).toBeVisible();await shot('rss-checked','#add-dialog');
   await r.page.locator('#rss-history-mode').selectOption('date');await shot('rss-date','#add-dialog');
   await r.page.locator('#rss-url').fill('https://example.org/error');await r.page.evaluate(()=>readerTransport.replies.set('/api/feeds/verify',{status:400,body:{detail:'Синтетическая ошибка проверки фида. '+ 'Длинное пояснение '.repeat(8)}}));
   await r.page.locator('#rss-check').click();await expect(r.page.locator('#rss-message')).toContainText('Синтетическая ошибка');await shot('rss-error','#add-dialog');
   await r.page.locator('#add-telegram-tab').click();await expect(r.page.locator('#telegram-account')).toHaveValue('synthetic');
   await r.page.locator('#telegram-peer').fill('@synthetic');
   await r.page.locator('#telegram-topics').click();await expect(r.page.locator('#telegram-topic-label')).toBeVisible();
   await r.page.locator('#telegram-topic').selectOption('30');
   expect(await r.page.locator('#telegram-auto').evaluate(n=>getComputedStyle(n).backgroundColor)).toBe('rgb(209, 209, 214)');
   await r.page.locator('#telegram-auto').check();
   await expect.poll(()=>r.page.locator('#telegram-auto').evaluate(n=>getComputedStyle(n).backgroundColor)).toBe('rgb(52, 199, 89)');
   await shot('telegram','#add-dialog');
   await r.page.locator('#add-opml-tab').click();await r.page.locator('#opml-file').setInputFiles({name:'sample.opml',mimeType:'text/xml',buffer:Buffer.from('<opml><body/></opml>')});await expect(r.page.locator('#import-confirm')).toBeVisible();await shot('opml','#add-dialog');
   await r.page.locator('#add-close').click();await expect(r.page.locator('#add-open')).toBeFocused();
   for(const id of ['plain','video','theses','hidden','digest','media']){
    await r.page.locator('[data-card="'+id+'"]').click();await expect(r.page.locator('#material')).toBeVisible();
    if(id==='media'){await expect(r.page.locator('#images img')).toBeVisible();await r.page.locator('#versions summary').click();await expect(r.page.locator('#versions')).toContainText('Ранее сохранённый текст');}
    if(id==='digest'){await r.page.locator('#event-sources summary').filter({hasText:'Оригинал'}).click();}
    expect(await r.page.locator('main>article').evaluate(n=>getComputedStyle(n).backgroundColor)).toBe('rgb(255, 255, 255)');
    if(id==='video')expect(await r.page.locator('#completeness').evaluate(n=>getComputedStyle(n).color)).toBe('rgb(137, 91, 31)');
    if(id==='plain')expect(await r.page.locator('#completeness').evaluate(n=>getComputedStyle(n).color)).toBe('rgb(99, 99, 102)');
    await shot(id,'main>article');
    if(id==='plain'){await r.page.locator('#original-panel summary').click();await shot('original-collapsed','main>article');}
   }
   await r.page.locator('[data-card="pending"]').click();await expect(r.page.locator('#empty')).toContainText('Загрузка');await shot('loading','main>article');
   await r.page.locator('[data-card="failed"]').click();await expect(r.page.locator('#empty')).toContainText('Синтетическая ошибка');await shot('error','main>article');
   // The intentional HTTP errors are UI feedback, not JS execution errors.
   expect(r.errors).toEqual([]);
  }finally{await r.close();}
 }
 await fs.writeFile(artifactPath('surfaces-geometry.json'),JSON.stringify(measurements,null,2));
 console.info('Compared geometry in',Object.keys(measurements).length,'states');
});
