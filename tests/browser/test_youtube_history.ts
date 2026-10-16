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
    await expect(r.page.locator('#history-toggle')).toHaveText('Продолжить историю');
    await expect(r.page.locator('#youtube-history-start')).toBeDisabled();
    await r.page.locator('#history-toggle').click();
    await r.page.waitForFunction(() => readerTransport.requests.some(request => request.method==='PATCH'&&request.url==='/api/v3/feeds/yt/history'));
    expect(await r.page.evaluate(()=>readerTransport.requests.find(x=>x.method==='PATCH'&&x.url==='/api/v3/feeds/yt/history')!.body)).toEqual({paused:false});
    await r.page.locator('#youtube-history-stop').click();
    await r.page.waitForFunction(() => readerTransport.requests.some(request => request.method==='DELETE'&&request.url==='/api/v3/feeds/yt/history'));
    expect(await r.page.evaluate(()=>readerTransport.requests.some(x=>x.method==='DELETE'&&x.url==='/api/v3/feeds/yt/history'))).toBe(true);
  } finally {await r.close();}
});

test('YouTube date selection explains that RSS does not provide the full archive', async()=>{
  const r=await reader();
  try {
    await r.start([]);
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/v3/feeds/verify',{body:{url:'https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa',title:'Видео',kind:'youtube',verification:'checked'}});
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa');
    await r.page.locator('#rss-check').click();
    await r.page.locator('#rss-history-mode').selectOption('date');
    await expect(r.page.locator('#rss-history-warning')).toContainText('История по дате ограничена доступным окном RSS');
    await r.page.locator('#rss-history-mode').selectOption('count');
    await expect(r.page.locator('#rss-history-warning')).toContainText('без Shorts');
    await expect(r.page.locator('#rss-history-warning')).not.toContainText('История по дате');
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

test('reopened first history gets progress and resume after a late response', async()=>{
 const r=await reader();
 try{
 await r.start([]);
 await r.page.evaluate(()=>{
  const t=readerTransport, settings=t.replies.get('/api/v3/settings/oko/global')!.body;
  const feed={id:'A',title:'A',kind:'youtube',transport:'youtube',folder_id:'f',original_urls:[],importance:'normal',initial_history:null};
  t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'f'}],feeds:[feed]}});
  t.replies.set('/api/v3/settings/feed/A',{body:{...settings,feed}});
  t.held.set('/api/v3/feeds/A/history',true);
 });
 await r.page.locator('#feeds-open').click(); await r.page.locator('[data-owner="feed/A"]').click();
    await feedOptions(r.page);
 await r.page.locator('#youtube-history-start').click();
 await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/feeds/A/history'));
 await expect(r.page.locator('#youtube-history-start')).toBeDisabled();
 await r.page.locator('#feeds-close').click(); await r.page.locator('#feeds-open').click();
    await feedOptions(r.page);
 await expect(r.page.locator('#youtube-history-start')).toBeEnabled();
 await r.page.evaluate(()=>{
  const t=readerTransport,history=window.readerFixtures.history({feed_id:'A',mode:'count',count:60,loaded:0,available:0,state:'waiting',paused:false,discovery_error:'limit'});
  t.replies.get('/api/v3/subscriptions')!.body!.feeds[0].initial_history=history;
  t.replies.set('/api/v3/status',{body:{stage:'Ожидание',errors:[],ready_sources:1,total_sources:1,initial_histories:[history],sources:[]}});
  t.release('/api/v3/feeds/A/history',history);
 });
 await expect(r.page.locator('#youtube-history-start')).toBeDisabled();
 await expect(r.page.locator('#history-toggle')).toBeVisible({timeout:1500});
 }finally{await r.close();}
});

test('saved history count retains exact decimal text when reopening settings',async()=>{
  const r=await reader();
  try {
    await r.start([]);
    await r.page.evaluate(()=>{
      const t=readerTransport,settings=t.replies.get('/api/v3/settings/oko/global')!.body;
      const feed={id:'large',title:'Large',kind:'youtube',transport:'youtube',folder_id:'f',original_urls:[],importance:'normal',
        initial_history:window.readerFixtures.history({mode:'count',count:9007199254740992,count_text:'9007199254740993',state:'done',loaded:0,available:0})};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'f'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/large',{body:{...settings,feed}});
      t.held.set('/api/v3/feeds/large/history',true);
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('[data-owner="feed/large"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#youtube-history-count')).toHaveValue('9007199254740993');
    await r.page.locator('#youtube-history-start').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/feeds/large/history'));
    expect(await r.page.evaluate(()=>readerTransport.requests.find(r=>r.method==='POST'&&r.url==='/api/v3/feeds/large/history')!.body!.count)).toBe('9007199254740993');
  } finally {await r.close();}
});
