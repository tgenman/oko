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

test('late start response cannot replace completed history progress', async()=>{
  const r=await reader();
  try{
    await r.start([]);
    await r.page.evaluate(()=>{
      const t=readerTransport, settings=t.replies.get('/api/v3/settings/oko/global')!.body;
      const feed={id:'yt',title:'Video',kind:'youtube',transport:'youtube',folder_id:'f',original_urls:['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa'],importance:'normal',initial_history:null};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Folder'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/yt',{body:{...settings,feed}});
      t.held.set('/api/v3/feeds/yt/history',true);
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('[data-owner="feed/yt"]').click();
    await feedOptions(r.page);
    await r.page.locator('#youtube-history-count').fill('3');
    await r.page.locator('#youtube-history-start').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/feeds/yt/history'));
    await r.page.evaluate(()=>{
      const t=readerTransport;
      const done=window.readerFixtures.history({mode:'count',count:3,loaded:3,available:3,state:'done',scan_complete:true,frozen:true});
      t.replies.get('/api/v3/subscriptions')!.body!.feeds[0].initial_history=done;
      t.replies.get('/api/v3/settings/feed/yt')!.body!.feed!.initial_history=done;
      t.replies.set('/api/v3/status',{body:{stage:'Готово',errors:[],sources:[],ready_sources:1,total_sources:1,initial_histories:[{...done,feed_id:'yt'}]}});
    });
    await expect(r.page.locator('#history-progress')).toContainText('Loaded 3 of 3');
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/status/details',true));
    await r.page.evaluate(()=>readerTransport.release('/api/v3/feeds/yt/history',window.readerFixtures.history({mode:'count',count:3,loaded:0,available:3,state:'pending'})));
    await expect(r.page.locator('#youtube-history-start')).toBeEnabled();
    await expect(r.page.locator('#history-progress')).toContainText('Loaded 3 of 3');
  }finally{await r.close();}
});

test('late settings response cannot replace a newer waiting history', async()=>{
  const r=await reader();
  try{
    await r.start([]);
    await r.page.evaluate(()=>{
      const t=readerTransport,settings=t.replies.get('/api/v3/settings/oko/global')!.body;
      const feed={id:'yt',title:'Video',kind:'youtube',transport:'youtube',folder_id:'f',original_urls:['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa'],importance:'normal',initial_history:null};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Folder'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/yt',{body:{...settings,feed}});
      t.replies.set('POST /api/v3/feeds/yt/history',{body:window.readerFixtures.history({mode:'count',count:3,loaded:0,available:0,state:'selecting'})});
    });
    await r.page.locator('#feeds-open').click();await r.page.locator('[data-owner="feed/yt"]').click();await feedOptions(r.page);
    await r.page.locator('#youtube-history-count').fill('3');
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/settings/feed/yt',true));
    await r.page.locator('#youtube-history-start').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/settings/feed/yt'));
    await r.page.evaluate(()=>{
      const t=readerTransport;
      const waiting=window.readerFixtures.history({mode:'count',count:3,loaded:0,available:0,state:'waiting',discovery_error:'YouTube недоступен'});
      t.replies.set('/api/v3/status',{body:{stage:'Готово',errors:[],sources:[],ready_sources:1,total_sources:1,initial_histories:[{...waiting,feed_id:'yt'}]}});
    });
    await expect(r.page.locator('#history-progress')).toContainText('YouTube недоступен');
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/status/details',true));
    await r.page.evaluate(()=>{
      const t=readerTransport,body=t.replies.get('/api/v3/settings/feed/yt')!.body!,old=structuredClone(body);
      body.feed!.initial_history=window.readerFixtures.history({mode:'count',count:3,loaded:0,available:0,state:'waiting',discovery_error:'YouTube недоступен'});
      t.release('/api/v3/settings/feed/yt',old);
    });
    await expect(r.page.locator('#settings-message')).toHaveText('History download started');
    await expect(r.page.locator('#history-progress')).toContainText('YouTube недоступен');
  }finally{await r.close();}
});

test('failed settings refresh preserves already completed history', async()=>{
  const r=await reader();
  try{
    await r.start([]);
    await r.page.evaluate(()=>{
      const t=readerTransport,settings=t.replies.get('/api/v3/settings/oko/global')!.body;
      const feed={id:'yt',title:'Video',kind:'youtube',transport:'youtube',folder_id:'f',original_urls:['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa'],importance:'normal',initial_history:null};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Folder'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/yt',{body:{...settings,feed}});
      t.held.set('/api/v3/feeds/yt/history',true);
    });
    await r.page.locator('#feeds-open').click();await r.page.locator('[data-owner="feed/yt"]').click();await feedOptions(r.page);
    await r.page.locator('#youtube-history-count').fill('3');await r.page.locator('#youtube-history-start').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/feeds/yt/history'));
    await r.page.evaluate(()=>{
      const t=readerTransport,done=window.readerFixtures.history({mode:'count',count:3,loaded:3,available:3,state:'done',scan_complete:true,frozen:true});
      t.replies.set('/api/v3/status',{body:{stage:'Готово',errors:[],sources:[],ready_sources:1,total_sources:1,initial_histories:[{...done,feed_id:'yt'}]}});
      t.held.set('/api/v3/settings/feed/yt',true);
    });
    await expect(r.page.locator('#history-progress')).toContainText('Loaded 3 of 3');
    await r.page.evaluate(()=>{
      readerTransport.held.set('/api/v3/status/details',true);
      readerTransport.release('/api/v3/feeds/yt/history',window.readerFixtures.history({mode:'count',count:3,loaded:0,available:3,state:'selecting'}));
    });
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/settings/feed/yt'));
    await r.page.evaluate(()=>readerTransport.release('/api/v3/settings/feed/yt',{},503));
    await expect(r.page.locator('#settings-message')).toHaveText('History download started');
    await expect(r.page.locator('#history-progress')).toContainText('Loaded 3 of 3');
  }finally{await r.close();}
});

test('confirmed stop wins over status observed before cancellation', async()=>{
  const r=await reader();
  try{
    await r.start([]);
    await r.page.evaluate(()=>{
      const t=readerTransport,settings=t.replies.get('/api/v3/settings/oko/global')!.body;
      const running=window.readerFixtures.history({mode:'count',count:3,loaded:0,available:3,state:'loading'});
      const feed={id:'yt',title:'Video',kind:'youtube',transport:'youtube',folder_id:'f',original_urls:['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa'],importance:'normal',initial_history:running};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Folder'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/yt',{body:{...settings,feed}});
      t.replies.set('/api/v3/status',{body:{stage:'Готово',errors:[],sources:[],ready_sources:1,total_sources:1,initial_histories:[{...running,feed_id:'yt'}]}});
      t.held.set('/api/v3/feeds/yt/history',true);
    });
    await r.page.locator('#feeds-open').click();await r.page.locator('[data-owner="feed/yt"]').click();await feedOptions(r.page);
    await r.page.locator('#youtube-history-stop').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/feeds/yt/history'));
    await r.page.evaluate(()=>{
      const t=readerTransport,running=window.readerFixtures.history({mode:'count',count:3,loaded:1,available:3,state:'loading'});
      t.replies.set('/api/v3/status',{body:{stage:'Готово',errors:[],sources:[],ready_sources:1,total_sources:1,initial_histories:[{...running,feed_id:'yt'}]}});
      t.held.set('/api/v3/settings/feed/yt',true);
    });
    await expect(r.page.locator('#history-progress')).toContainText('Loaded 1 of 3');
    await r.page.evaluate(()=>{
      readerTransport.held.set('/api/v3/status/details',true);
      readerTransport.release('/api/v3/feeds/yt/history',window.readerFixtures.history({mode:'count',count:3,loaded:1,available:3,state:'cancelled',cancelled:true}));
    });
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/settings/feed/yt'));
    await r.page.evaluate(()=>readerTransport.release('/api/v3/settings/feed/yt',{},503));
    await expect(r.page.locator('#settings-message')).toHaveText('History download stopped');
    await expect(r.page.locator('#youtube-history-start')).toBeEnabled();
  }finally{await r.close();}
});

for(const intervening of ['old','newer','newer_fetched'] as const) test(`${intervening} status during history refresh keeps the latest run`, async()=>{
  const r=await reader();
  try{
    await r.start([]);
    await r.page.evaluate(()=>{
      const t=readerTransport,settings=t.replies.get('/api/v3/settings/oko/global')!.body;
      const old=window.readerFixtures.history({mode:'count',count:1,loaded:0,available:1,state:'done',scan_complete:true,frozen:true,run:'old'});
      const fresh=window.readerFixtures.history({mode:'count',count:3,loaded:3,available:3,state:'done',scan_complete:true,frozen:true,run:'new'});
      const feed={id:'yt',title:'Video',kind:'youtube',transport:'youtube',folder_id:'f',original_urls:['https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa'],importance:'normal',initial_history:old};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Folder'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/yt',{body:{...settings,feed}});
      t.replies.set('POST /api/v3/feeds/yt/history',{body:fresh});
    });
    await r.page.locator('#feeds-open').click();await r.page.locator('[data-owner="feed/yt"]').click();await feedOptions(r.page);
    await expect(r.page.locator('#history-progress')).toContainText('Loaded 0 of 1');
    await r.page.locator('#youtube-history-count').fill('3');
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/settings/feed/yt',true));
    await r.page.locator('#youtube-history-start').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/settings/feed/yt'));
    await r.page.evaluate((kind)=>{
      const t=readerTransport,history=window.readerFixtures.history({mode:'count',count:kind==='old'?1:7,loaded:kind==='old'?1:7,available:kind==='old'?1:7,state:'done',scan_complete:true,frozen:true,run:kind==='old'?'old':'later'});
      t.replies.set('/api/v3/status',{body:{stage:'Готово',errors:[],sources:[],ready_sources:1,total_sources:1,initial_histories:[{...history,feed_id:'yt'}]}});
    },intervening);
    await expect(r.page.locator('#history-progress')).toContainText(intervening==='old'?'Loaded 1 of 1':'Loaded 7 of 7');
    await r.page.evaluate((kind)=>{
      const t=readerTransport,body=t.replies.get('/api/v3/settings/feed/yt')!.body!;
      body.feed!.initial_history=window.readerFixtures.history({mode:'count',count:3,loaded:3,available:3,state:'done',scan_complete:true,frozen:true,run:'new'});
      if(kind==='newer_fetched')body.feed!.initial_history=window.readerFixtures.history({mode:'count',count:9,loaded:9,available:9,state:'done',scan_complete:true,frozen:true,run:'latest'});
      const captured=structuredClone(body);
      if(kind==='newer')body.feed!.initial_history=window.readerFixtures.history({mode:'count',count:7,loaded:7,available:7,state:'done',scan_complete:true,frozen:true,run:'later'});
      t.held.set('/api/v3/status/details',true);t.release('/api/v3/settings/feed/yt',captured);
    },intervening);
    await expect(r.page.locator('#settings-message')).toHaveText('History download started');
    await expect(r.page.locator('#history-progress')).toContainText(intervening==='old'?'Loaded 3 of 3':intervening==='newer'?'Loaded 7 of 7':'Loaded 9 of 9');
  }finally{await r.close();}
});
