import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {reader, expect} from './support.ts';

test('YouTube settings start N history and waiting resumes manually without changing other feed', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id:'yt',title:'Видео',kind:'youtube',transport:'youtube',folder_id:'f',original_urls:['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa'],importance:'normal',initial_history:null};
      const settings = t.replies.get('/api/v3/settings/oko/global')!.body;
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/yt',{body:{...settings,feed}});
      const history = window.readerFixtures.history({mode:'count',count:60,loaded:0,available:3,incomplete:0,state:'waiting',paused:false,scan_complete:false,discovery_error:'YouTube недоступен'});
      t.replies.set('POST /api/v3/feeds/yt/history',{body:history});
      t.replies.set('PATCH /api/v3/feeds/yt/history',{body:{...history,state:'selecting'}});
      t.replies.set('DELETE /api/v3/feeds/yt/history',{body:{...history,state:'cancelled',cancelled:true}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/yt"]').click();
    await feedOptions(r.page);
    await r.page.locator('#youtube-history-count').fill('60');
    await r.page.locator('#youtube-history-start').click();
    await r.page.waitForFunction(() => readerTransport.requests.some(request => request.method==='POST'&&request.url==='/api/v3/feeds/yt/history'));
    const request = await r.page.evaluate(() => readerTransport.requests.find(x=>x.method==='POST'&&x.url==='/api/v3/feeds/yt/history'));
    expect(request!.body).toEqual({mode:'count',count:'60'});
    await r.page.evaluate(() => {
      const t=readerTransport;
      const history=t.replies.get('POST /api/v3/feeds/yt/history')!.body;
      t.replies.get('/api/v3/subscriptions')!.body!.feeds[0].initial_history=history;
    });
    await r.page.locator('#feeds-close').click();
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/yt"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#history-progress')).toContainText('YouTube недоступен');
    await expect(r.page.locator('#history-toggle')).toHaveCount(0);
    await expect(r.page.locator('#youtube-history-start')).toBeDisabled();
    await r.page.locator('#youtube-history-stop').click();
    await r.page.waitForFunction(() => readerTransport.requests.some(request => request.method==='DELETE'&&request.url==='/api/v3/feeds/yt/history'));
    expect(await r.page.evaluate(()=>readerTransport.requests.some(x=>x.method==='DELETE'&&x.url==='/api/v3/feeds/yt/history'))).toBe(true);
  } finally {await r.close();}
});

test('late history start preserves selection and input in another feed', async()=>{
  const r=await reader();
  try {
    await r.start([]);
    await r.page.evaluate(()=>{
      const t=readerTransport, settings=t.replies.get('/api/v3/settings/oko/global')!.body;
      const feeds=['A','B'].map(id=>({id,title:id,kind:'youtube',transport:'youtube',folder_id:'f',original_urls:['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa'],importance:'normal',initial_history:null}));
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds}});
      for(const feed of feeds)t.replies.set('/api/v3/settings/feed/'+feed.id,{body:{...settings,feed}});
      t.held.set('/api/v3/feeds/A/history',true);
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('[data-owner="feed/A"]').click();
    await feedOptions(r.page);
    await r.page.locator('#youtube-history-count').fill('60');
    await r.page.locator('#youtube-history-start').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/feeds/A/history'));
    await r.page.locator('[data-owner="feed/B"]').click();
    await feedOptions(r.page);
    await r.page.locator('#youtube-history-count').fill('13');
    await r.page.evaluate(()=>readerTransport.release('/api/v3/feeds/A/history',{mode:'count',count:60,state:'pending'}));
    await expect(r.page.locator('#owner-heading')).toContainText('B');
    await expect(r.page.locator('#youtube-history-count')).toHaveValue('13');
    await expect(r.page.locator('#youtube-history-start')).toBeEnabled();
  } finally {await r.close();}
});
