const {test} = require('node:test');
const {reader, expect} = require('./support.cjs');

test('history can pause and resume in feed settings without offering restart after completion', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const history = {mode:'count',count:20,count_text:'20',loaded:7,available:20,state:'loading',frozen:true,paused:false};
      const feed = {id:'rss',title:'История',kind:'rss',transport:'rss',folder_id:'f',original_urls:['https://source.test/feed'],importance:'normal',initial_days:1,initial_history:history};
      t.replies.set('/api/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds:[feed]}});
      t.replies.set('/api/settings/feed/rss',{body:{...t.replies.get('/api/settings/oko/global').body,feed}});
      t.replies.set('/api/feeds/rss/history',{body:{...history,paused:true,state:'paused'}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#settings-owner').selectOption('feed/rss');
    await r.page.getByRole('button',{name:'Приостановить историю',exact:true}).click();
    await expect(r.page.locator('#history-progress')).toContainText('приостановлена');
    await expect(r.page.getByRole('button',{name:'Продолжить историю',exact:true})).toBeVisible();
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url==='/api/feeds/rss/history'));
    expect(request.body).toEqual({paused:true});
    await r.page.evaluate(() => readerTransport.replies.set('/api/feeds/rss/history',{body:{mode:'count',count:20,count_text:'20',loaded:20,available:20,state:'done',frozen:true,paused:false}}));
    await r.page.getByRole('button',{name:'Продолжить историю',exact:true}).click();
    await expect(r.page.locator('#history-progress')).toContainText('завершена');
    await expect(r.page.locator('#history-toggle')).toBeHidden();
  } finally {await r.close();}
});
