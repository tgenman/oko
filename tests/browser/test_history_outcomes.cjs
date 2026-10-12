const {test} = require('node:test');
const {reader, expect} = require('./support.cjs');

test('initial history shows partial, retry and unavailable outcomes with errors', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id:'rss',title:'История',kind:'rss',transport:'rss',folder_id:'f',original_urls:['https://source.test/feed'],importance:'normal',initial_days:1,
        initial_history:{mode:'count',count:20,loaded:18,incomplete:2,retrying:1,unavailable:1,state:'loading',frozen:true,available:20,paused:false,errors:[{message:'Источник недоступен'}]}};
      t.replies.set('/api/subscriptions',{body:{folders:[{id:'f',title:'Учёба'}],feeds:[feed]}});
      t.replies.set('/api/settings/feed/rss',{body:{...t.replies.get('/api/settings/oko/global').body,feed}});
    });
    await r.page.locator('#settings-open').click();
    await r.page.locator('#settings-owner').selectOption('feed/rss');
    await expect(r.page.locator('#feed-options')).toContainText('Загружено 18 из 20');
    await expect(r.page.locator('#feed-options')).toContainText('2 неполных');
    await expect(r.page.locator('#feed-options')).toContainText('1 ожидает повторной попытки');
    await expect(r.page.locator('#feed-options')).toContainText('1 окончательно недоступно');
    await expect(r.page.locator('#feed-options')).toContainText('Источник недоступен');
  } finally {await r.close();}
});
