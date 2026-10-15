import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {reader, expect} from './support.ts';

test('initial history shows partial, retry and unavailable outcomes with errors', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id:'rss',title:'История',kind:'rss',transport:'rss',folder_id:'f',original_urls:['https://source.test/feed'],importance:'normal',initial_days:1,
        initial_history:window.readerFixtures.history({mode:'count',count:20,loaded:18,incomplete:2,retrying:1,unavailable:1,state:'loading',frozen:true,available:20,paused:false,errors:[{item:'unavailable',message:'Источник недоступен'}]})};
      t.replies.set('/api/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds:[feed]}});
      t.replies.set('/api/settings/feed/rss',{body:{...t.replies.get('/api/settings/oko/global')!.body,feed}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).toContainText('Загружено 18 из 20');
    await expect(r.page.locator('#feed-options')).toContainText('2 неполных');
    await expect(r.page.locator('#feed-options')).toContainText('1 ожидает повторной попытки');
    await expect(r.page.locator('#feed-options')).toContainText('1 окончательно недоступно');
    await expect(r.page.locator('#feed-options')).toContainText('Источник недоступен');
  } finally {await r.close();}
});

test('count selection does not announce shortfall before metadata scan completes', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id:'rss',title:'История',kind:'telegram',transport:'telegram',folder_id:'f',original_urls:['@source'],importance:'normal',
        initial_history:window.readerFixtures.history({mode:'count',count:100,loaded:0,state:'selecting',frozen:false,scan_complete:false,available:10,paused:false,discovery_error:'Архив недоступен',discovery_retry_at:Date.now()/1000+600})};
      t.replies.set('/api/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds:[feed]}});
      t.replies.set('/api/settings/feed/rss',{body:{...t.replies.get('/api/settings/oko/global')!.body,feed}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).not.toContainText('доступно 10');
    await expect(r.page.locator('#feed-options')).toContainText('Выбираются публикации начальной истории');
    await expect(r.page.locator('#feed-options')).toContainText('Архив недоступен');
    await expect(r.page.locator('#feed-options')).toContainText('повторная попытка');
  } finally {await r.close();}
});
