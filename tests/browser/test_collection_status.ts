import {test} from 'node:test';
import {reader, expect, material} from './support';
import {artifactPath} from './artifacts';
import type {CollectionSnapshot, CollectionDetails} from '../../frontend/src/api/generated/types.gen';

const operation:CollectionDetails['operations'][number]={operation_id:'42',attempt_id:'42:2',kind:'fetch',state:'waiting',stage:'Загрузка вложения',reason:'Источник ограничил запросы',elapsed_seconds:252,time_kind:'waiting',queued_at:'2026-10-21T18:00:00Z',started_at:'2026-10-21T18:00:00Z',finished_at:'2026-10-21T18:01:00Z',retry_at:'2026-10-21T18:10:00Z',last_error:null};
const source:CollectionSnapshot['sources'][number]={feed_id:'feed',title:'Архив лекций',source_url:'https://example.org/rss',source_kind:'rss',access:'rss',state:'waiting',operation,operation_count:1,error_count:1,waiting_count:1,last_success_at:null,last_success_seconds:null,has_details:true,history:null};
const snapshot:CollectionSnapshot={instance_id:'fixture',revision:1,observed_at:'2026-10-21T18:04:12Z',work_allowed:true,counts:{collecting:0,waiting:1,queued:0,error:0,paused:0,complete:0,never:0,deleting:0,unavailable:0},sources:[source]};
const details:CollectionDetails={instance_id:'fixture',revision:2,observed_at:snapshot.observed_at,work_allowed:true,source,operations:[{...operation,last_error:{attempt_id:'42:2',operation_id:'42',occurred_at:'2026-10-21T18:01:00Z',error_type:'ReadTimeout',message:'<script>throw new Error("unsafe")</script> timed out',traceback:'ReadTimeout: timed out',source:'server',message_truncated:false,traceback_truncated:false,resolved:false,requests:[{url:'https://example.org/media/video.mp4',original_url:null,method:'GET',purpose:'attachment',occurred_at:1800000000,http_status:null,error_type:'ReadTimeout',message:'timed out',count:1,first_at:1800000000,last_at:1800000000,redacted:false,message_truncated:false}],request_count:1}}],last_error:null};

test('collection dialog explains a waiting source, shows raw errors and preserves reading and focus',async()=>{
 const r=await reader();
 try{
  await r.start([material('one')]);
  await r.page.evaluate(({snapshot,details})=>{readerTransport.replies.set('/api/v3/collection-status',{body:snapshot});readerTransport.replies.set('/api/v3/collection-status/feed',{body:details});},{snapshot,details});
  await r.page.locator('#cards button').click();
  const trigger=r.page.getByRole('button',{name:/Архив лекций.*Подробнее/});
  await trigger.click();
  const dialog=r.page.getByRole('dialog',{name:'Обновление источников'});
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Архив лекций');
  await dialog.getByRole('button',{name:'Подробности',exact:true}).click();
  await expect(dialog).toContainText('<script>throw new Error("unsafe")</script> timed out');
  await expect(dialog.getByRole('link',{name:'https://example.org/media/video.mp4',exact:true})).toHaveAttribute('target','_blank');
  await expect(dialog.locator('script')).toHaveCount(0);
  await dialog.getByRole('searchbox',{name:'Найти источник'}).fill('несуществующий');
  await expect(dialog).toContainText('Нет совпадений');
  await dialog.getByRole('searchbox',{name:'Найти источник'}).fill('');
  await expect(dialog).toContainText('ReadTimeout');
  const path=artifactPath('source-collection-desktop.png');await r.page.screenshot({path});console.info('Collection status screenshot:',path);
  await r.page.keyboard.press('Escape');await expect(dialog).toBeHidden();await expect(trigger).toBeFocused();
  await expect(r.page.locator('#title')).toHaveText('one');
  await trigger.click();await expect(dialog).toContainText('ReadTimeout');
  await r.page.setViewportSize({width:390,height:844});
  const mobile=artifactPath('source-collection-mobile.png');await r.page.screenshot({path:mobile});console.info('Collection status mobile:',mobile);
  await dialog.getByRole('button',{name:'Закрыть'}).click();await expect(dialog).toBeHidden();
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('late collection details cannot overwrite reopened dialog and clipboard preserves original text',async()=>{
 const r=await reader();
 try{
  await r.context.grantPermissions(['clipboard-read','clipboard-write']);
  await r.start([]);
  await r.page.evaluate(snapshot=>{readerTransport.replies.set('/api/v3/collection-status',{body:snapshot});readerTransport.held.set('/api/v3/collection-status/feed',true);},snapshot);
  const trigger=r.page.locator('#collection-progress');
  await expect(trigger).toContainText('Архив лекций');await trigger.click();
  const dialog=r.page.getByRole('dialog',{name:'Обновление источников'});
  await dialog.getByRole('button',{name:'Подробности',exact:true}).click();
  await expect.poll(()=>r.page.evaluate(()=>readerTransport.pending.has('/api/v3/collection-status/feed'))).toBe(true);
  await r.page.evaluate(details=>{const resolve=readerTransport.pending.get('/api/v3/collection-status/feed');window.releaseOldMenu=()=>resolve!(new Response(JSON.stringify(details),{headers:{'Content-Type':'application/json'}}));readerTransport.pending.delete('/api/v3/collection-status/feed');readerTransport.held.delete('/api/v3/collection-status/feed');const fresh=structuredClone(details);fresh.revision=10;fresh.operations[0]!.last_error!.message='fresh error\nsecond line';readerTransport.replies.set('/api/v3/collection-status/feed',{body:fresh});},details);
  await r.page.keyboard.press('Escape');await trigger.click();
  await expect(dialog).toContainText('fresh error');
  await r.page.evaluate(()=>window.releaseOldMenu());
  await expect(dialog).toContainText('fresh error');
  await expect(dialog).not.toContainText('unsafe');
  await dialog.getByRole('button',{name:'Копировать ошибку',exact:true}).click();
  expect(await r.page.evaluate(()=>navigator.clipboard.readText())).toContain('fresh error\nsecond line');
  await dialog.getByRole('button',{name:'Копировать ссылку',exact:true}).click();
  expect(await r.page.evaluate(()=>navigator.clipboard.readText())).toBe('https://example.org/media/video.mp4');
  await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/collection-status',{status:503,body:{code:'storage_unavailable',message:'offline',details:null,diagnostic_id:null,command_id:null}}));
  await expect(dialog).toContainText('Нет связи с Око');
  await expect(dialog).toContainText('fresh error');
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
