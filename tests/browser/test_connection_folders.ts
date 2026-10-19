import {test} from 'node:test';
import {reader, expect} from './support';
import {artifactPath} from './artifacts';

for (const folders of [[], [{id:'science',title:'Наука'}]]) {
 test(`connection folder choices contain only existing folders (${folders.length})`,async()=>{
  const r=await reader();
  try {
   await r.start([]);
   await r.page.evaluate(folders=>{
    readerTransport.replies.set('/api/v3/subscriptions',{body:{folders,feeds:[]}});
    readerTransport.replies.set('/api/v3/feeds/verify',{body:{state:'new',verification:'checked',candidate:{url:'https://source.test/feed',title:'Источник',kind:'rss'}}});
   },folders);
   await r.page.locator('#add-open').click();
   await r.page.locator('#rss-url').fill('https://source.test/feed');
   await r.page.locator('#rss-check').click();
   for(const prefix of ['rss','telegram']) {
    await r.page.locator('#add-'+prefix+'-tab').click();
    await expect(r.page.locator('#'+prefix+'-folder option')).toHaveText([...folders.map(f=>f.title),'Create New…']);
    await expect(r.page.locator('#'+prefix+'-folder-new')).toBeVisible();
    if(prefix==='telegram'){
     await r.page.locator('#telegram-peer').fill('@channel');
     await expect(r.page.locator('#telegram-add')).toBeDisabled();
    }
    await r.page.locator('#'+prefix+'-folder-new').fill('Моя папка');
    if(prefix==='telegram')await expect(r.page.locator('#telegram-add')).toBeEnabled();
    if(folders.length){
     await r.page.locator('#'+prefix+'-folder').selectOption('Наука');
     await expect(r.page.locator('#'+prefix+'-folder-new')).toBeHidden();
    }
   }
   if(!folders.length){const file=artifactPath('telegram-no-phantom-folder.png');await r.page.screenshot({path:file});console.info('Folder screenshot:',file);}
   expect(r.errors).toEqual([]);
  } finally {await r.close();}
 });
}
