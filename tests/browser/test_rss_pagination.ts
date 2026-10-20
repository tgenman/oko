import {test} from 'node:test';
import {reader, expect} from './support.ts';
import {feedOptions} from './settings-actions.ts';
import {artifactPath} from './artifacts.ts';

test('RSS settings show the shortage reason and can request count or inclusive date history', async () => {
 const r=await reader();
 try {
  await r.start([]);
  await r.page.evaluate(()=>{
   const t=readerTransport;
   const history={state:'complete',run:'rss-run',selection:{mode:'count',count:'200'},progress:{loaded:20,viewed:0,incomplete:0,available:20,retrying:0,unavailable:0,requests:2,errors:[],scan_complete:true,manifest_complete:true,warnings:[],discovery_problem:null,stop_reason:'no_continuation'}};
   const feed={id:'rss',title:'RSS history',kind:'rss',transport:'rss',folder_id:'f',original_urls:['https://source.test/feed'],importance:'normal',initial_history:history};
   t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'News'}],feeds:[feed]}});
   t.replies.set('/api/v3/settings/feed/rss',{body:{...t.replies.get('/api/v3/settings/oko/global')!.body,feed}});
   t.replies.set('POST /api/v3/feeds/rss/history',{body:history});
  });
  await r.page.locator('#feeds-open').click();
  await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
  await feedOptions(r.page);
  await expect(r.page.locator('#history-progress')).toContainText('Found 20 of 200');
  await expect(r.page.locator('#history-progress')).toContainText('The source provides no continuation');
  await expect(r.page.locator('#history-progress')).not.toContainText('Initial history complete');
  for(const [width,height,name] of [[1280,900,'rss-history-desktop.png'],[390,844,'rss-history-mobile.png']] as const){
   await r.page.setViewportSize({width,height});
   await r.page.screenshot({path:artifactPath(name),fullPage:true});
  }
  await r.page.locator('#rss-load-history-count').fill('200');
  await r.page.locator('#rss-load-history-start').click();
  await r.page.waitForFunction(()=>readerTransport.requests.some(r=>r.method==='POST'&&r.url==='/api/v3/feeds/rss/history'));
  expect(await r.page.evaluate(()=>readerTransport.requests.find(r=>r.method==='POST'&&r.url==='/api/v3/feeds/rss/history')!.body)).toEqual({mode:'count',count:'200'});
  await r.page.locator('#rss-load-history-mode').selectOption('date');
  await r.page.locator('#rss-load-history-date').fill('2025-10-01');
  await r.page.locator('#rss-load-history-start').click();
  await r.page.waitForFunction(()=>readerTransport.requests.filter(r=>r.method==='POST'&&r.url==='/api/v3/feeds/rss/history').length===2);
  expect(await r.page.evaluate(()=>readerTransport.requests.filter(r=>r.method==='POST'&&r.url==='/api/v3/feeds/rss/history').at(-1)!.body)).toEqual({mode:'date',date:'2025-10-01',timezone:await r.page.evaluate(()=>Intl.DateTimeFormat().resolvedOptions().timeZone)});
 } finally {await r.close();}
});
