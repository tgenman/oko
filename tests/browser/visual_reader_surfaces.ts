import {test} from 'node:test';
import {reader, material, expect} from './support.ts';
import {artifactPath} from './artifacts.ts';
import * as fs from 'node:fs/promises';
test('synthetic reader surfaces screenshots and geometry',async()=>{
 type Geometry = Record<string, string | number>;
 const measurements: Record<string, Geometry[]>={};
 for(const width of [1440]){
  const r=await reader();try{
   await r.page.setViewportSize({width,height:900});
   const points={points:[{text:'Основной тезис синтетического материала',sources:[{id:'plain',url:'https://example.org/plain'}]}]};
   const source=material('source',{});
   await r.start([
    material('plain',{title:'We Finally Know More About Humanity’s First Civilization',text:'Сохранённый текст публикации.\n\n'+('Читаем без изменения вёрстки. '.repeat(20)),published:1801991220}),
    material('video',{title:'Видео с недоступными субтитрами',captions:'unavailable',full:false,reason:'YouTube ограничил получение субтитров',description:'Описание видео\n\n'+('Текст и длинная ссылка https://example.org/'+'example/'.repeat(12)+'\n').repeat(3),text:''}),
    material('theses',{theses:points,provenance:{processor:'codex',model:'synthetic',reasoning_effort:'high',fast:false}}),
    material('hidden',{filter:{status:'done',hidden:true,reason:'Синтетический фильтр',removed:[]}}),
    material('media',{images:['/api/v3/image']}),
    material('pending'),material('failed')
   ]);
   await r.page.evaluate(()=>{
    const t=readerTransport;
    t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Наука'}],feeds:[]}});
    t.replies.set('/api/v3/telegram/accounts',{body:['synthetic']});
    t.replies.set('/api/v3/telegram/topics',{body:[{id:1,title:'Общее'},{id:30,title:'Наука'}]});
    t.replies.set('/api/v3/import/preview',{body:{folders:[{title:'Наука'}],feeds:[]}});
    t.replies.set('/api/v3/feeds/verify',{body:{url:'https://example.org/feed.xml',title:'Синтетический источник',kind:'rss',verification:'verified'}});
    t.replies.set('/api/v3/image',{mime:'image/png',base64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR9sAAAAASUVORK5CYII='});
    t.held.set('/fixture/material/pending',true);
    t.replies.set('/fixture/material/failed',{status:503,body:{detail:'Синтетическая ошибка открытия'}});
   });
   async function shot(name: string,scope: string){
    await r.page.locator(scope).scrollIntoViewIfNeeded();
    // Rendering is stable after two frames; no arbitrary fixed sleeps.
    await r.page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const key=name+'-'+width;
    const geometry=()=>r.page.locator(scope).evaluate(n=>[n,...n.querySelectorAll('*')].filter(e=>e.getClientRects().length).map((e): Geometry=>{
      const b=e.getBoundingClientRect(),s=getComputedStyle(e);return {tag:e.tagName,id:e.id,x:b.x,y:b.y,w:b.width,h:b.height,padding:s.padding,margin:s.margin,gap:s.gap,font:s.fontSize,weight:s.fontWeight,line:s.lineHeight,spacing:s.letterSpacing,display:s.display,overflow:s.overflow};
    }));
    measurements[key]=await geometry();
    const sheet=r.page.locator('link[data-reader-surfaces]');
    await sheet.evaluate(n=>{(n as HTMLLinkElement).disabled=true;});
    const original=await geometry();
    await sheet.evaluate(n=>{(n as HTMLLinkElement).disabled=false;});
    expect(measurements[key].length).toBe(original.length);
    for(let i=0;i<original.length;i++)for(const prop of Object.keys(original[i])){
      if(['x','y','w','h'].includes(prop))expect(Math.abs(Number(measurements[key][i][prop])-Number(original[i][prop])),key+' '+i+' '+prop).toBeLessThanOrEqual(1);
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
   await r.page.locator('#rss-url').fill('https://example.org/error');await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/feeds/verify',{status:400,body:{detail:'Синтетическая ошибка проверки фида. '+ 'Длинное пояснение '.repeat(8)}}));
   await r.page.locator('#rss-check').click();await expect(r.page.locator('#rss-message')).toContainText('Синтетическая ошибка');await shot('rss-error','#add-dialog');
   await r.page.locator('#add-telegram-tab').click();await expect(r.page.locator('#telegram-access')).toHaveValue('web');
   await r.page.locator('#telegram-peer').fill('@synthetic');
   await expect(r.page.locator('#telegram-topic')).toHaveCount(0);
   await expect(r.page.locator('#telegram-auto')).toBeHidden();
   await shot('telegram','#add-dialog');
   await r.page.locator('#add-close').click();await expect(r.page.locator('#add-open')).toBeFocused();
   for(const id of ['plain','video','theses','hidden','media']){
    await r.page.locator('[data-card="'+id+'"]').click();await expect(r.page.locator('#material')).toBeVisible();
    if(id==='media'){await expect(r.page.locator('#images img')).toBeVisible();await r.page.locator('#versions summary').click();await expect(r.page.locator('#versions')).toContainText('Ранее сохранённый текст');}
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
