import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {reader, expect} from './support.ts';
import {artifactPath} from './artifacts.ts';

test('initial history shows partial, retry and unavailable outcomes with errors', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id:'rss',title:'История',kind:'rss',transport:'rss',folder_id:'f',original_urls:['https://source.test/feed'],importance:'normal',initial_days:1,
        initial_history:window.readerFixtures.history({mode:'count',count:20,loaded:18,incomplete:2,retrying:1,unavailable:1,state:'loading',frozen:true,available:20,paused:false,errors:[{item:'unavailable',message:'Источник недоступен'}]})};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/rss',{body:{...t.replies.get('/api/v3/settings/oko/global')!.body,feed}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).toContainText('Loaded 18 of 20');
    await expect(r.page.locator('#feed-options')).toContainText('2 incomplete');
    await expect(r.page.locator('#feed-options')).toContainText('1 awaiting retry');
    await expect(r.page.locator('#feed-options')).toContainText('1 permanently unavailable');
    await expect(r.page.locator('#feed-options')).toContainText('Источник недоступен');
    await r.page.screenshot({path: artifactPath('history-outcomes.png')});
  } finally {await r.close();}
});

test('count selection does not announce shortfall before metadata scan completes', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id:'rss',title:'История',kind:'telegram',transport:'telegram',access:{kind:'unconfigured',source_locator:'@source'},connection:'needs_connection',folder_id:'f',original_urls:['@source'],importance:'normal',
        initial_history:window.readerFixtures.history({mode:'count',count:100,loaded:0,state:'selecting',frozen:false,scan_complete:false,available:10,paused:false,discovery_error:'Архив недоступен',discovery_retry_at:Date.now()/1000+600})};
      t.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds:[feed]}});
      t.replies.set('/api/v3/settings/feed/rss',{body:{...t.replies.get('/api/v3/settings/oko/global')!.body,feed}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).not.toContainText('available 10');
    await expect(r.page.locator('#feed-options')).toContainText('Discovering initial history');
    await expect(r.page.locator('#feed-options')).toContainText('Архив недоступен');
    await expect(r.page.locator('#feed-options')).toContainText('retry after');
  } finally {await r.close();}
});
