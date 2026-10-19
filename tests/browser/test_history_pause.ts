import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {reader, expect} from './support.ts';

test('unfinished history shows progress without offering a manual pause', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const history = window.readerFixtures.history({mode:'count',count:20,count_text:'20',loaded:7,available:20,state:'loading',frozen:true,paused:false});
      const feed = {id:'rss',title:'История',kind:'rss',transport:'rss',folder_id:'f',original_urls:['https://source.test/feed'],importance:'normal',initial_days:1,initial_history:history};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/rss',{body:{...t.replies.get('/api/v3/settings/oko/global')!.body,feed}});
      t.replies.set('/api/v3/feeds/rss/history',{body:{...history,paused:true,state:'paused'}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#history-progress')).toContainText('Loaded 7 of 20');
    await expect(r.page.locator('#history-toggle')).toHaveCount(0);
    expect(await r.page.evaluate(() => readerTransport.requests.some(r => r.url==='/api/v3/feeds/rss/history'))).toBe(false);
  } finally {await r.close();}
});
