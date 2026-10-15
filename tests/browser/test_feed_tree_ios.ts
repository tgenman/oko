import {test} from 'node:test';
import {reader, expect} from './support.ts';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), 'oko-feed-tree-ios-'));
const artifactPath = (name: string) => path.join(screenshots, name);
console.log('Screenshots:', screenshots);

test('feed settings independently expand folders and return to the selected object after catalog refresh', async () => {
  const r = await reader();
  try {
    await r.page.setViewportSize({width:390,height:844});
    const data = await r.api('import','POST',{opml:'<opml><body><outline text="Наука"><outline text="Альфа — длинное название источника для двух строк" xmlUrl="https://source.test/a"/><outline text="Бета" xmlUrl="https://source.test/b"/></outline><outline text="Физика"><outline text="Гамма" xmlUrl="https://source.test/c"/></outline></body></opml>'});
    await r.start([]);
    await r.page.evaluate(() => readerTransport.native.add('/api/subscriptions'));
    await r.page.locator('#feeds-open').click();
    const tree = r.page.locator('#feed-tree');
    const science = tree.getByRole('button',{name:'Свернуть Наука',exact:true});
    await science.click();
    await expect(tree.getByRole('button',{name:'Бета',exact:true})).toBeHidden();
    await expect(r.page.locator('#feed-list')).toBeVisible();
    await expect(tree.getByRole('button',{name:'Свернуть Физика',exact:true})).toHaveAttribute('aria-expanded','true');
    await tree.getByRole('button',{name:'Раскрыть Наука',exact:true}).click();
    const alpha = tree.getByRole('button',{name:'Альфа — длинное название источника для двух строк',exact:true});
    await alpha.click();
    await expect(r.page.locator('#owner-heading')).toHaveText('Фид: Альфа — длинное название источника для двух строк');
    await r.page.locator('#feeds-back').click();
    await expect(alpha).toBeFocused();
    await tree.getByRole('button',{name:'Свернуть Физика',exact:true}).click();
    await r.page.locator('#feeds-close').click();
    await r.page.locator('#feeds-open').click();
    // The refreshed catalog retains independent expansion and alphabetical ordering.
    await r.page.locator('#feeds-back').click();
    await expect(tree.getByRole('button',{name:'Раскрыть Физика',exact:true})).toHaveAttribute('aria-expanded','false');
    expect(await tree.locator('[data-owner^="feed/"]').evaluateAll(ns=>ns.map(n=>n.getAttribute('title')))).toEqual(['Альфа — длинное название источника для двух строк','Бета','Гамма']);
    expect(await r.page.locator('#feeds-dialog').evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true);
    await r.page.screenshot({path:artifactPath('feed-tree-ios-settings-mobile.png')});
    expect((await r.api('subscriptions')).feeds).toHaveLength(data.feeds.length);
    expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>['PATCH','PUT','POST','DELETE'].includes(q.method) && !q.url.includes('/tabs/') && !/^\/api\/v3\/client-sessions(?:\/[^/?]+)?$/.test(q.url) && q.url !== '/api/debug'))).toEqual([]);
    await r.page.setViewportSize({width:1280,height:900});
    await r.page.screenshot({path:artifactPath('feed-tree-ios-settings-desktop.png')});
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('reading tree has separate trailing disclosure controls and usable two-line titles at minimum width', async () => {
  const r = await reader();
  try {
    await r.page.setViewportSize({width:390,height:844});
    await r.start([]);
    await r.page.evaluate(() => readerTransport.replies.set('/api/navigation',{body:{count:79,folders:[{id:'f',title:'Наука',count:52},{id:'p',title:'Физика',count:27}],feeds:[{id:'long',title:'Kurzgesagt — In a Nutshell, научный канал',kind:'youtube',folder_id:'f',count:26}]}}));
    const folder = r.page.locator('#folders [data-folder="f"]');
    await expect(folder).toContainText('Наука');
    const toggle = r.page.locator('#folders').getByRole('button',{name:'Свернуть Наука',exact:true});
    const fb = await folder.boundingBox(), tb = await toggle.boundingBox();
    expect(tb!.x).toBeGreaterThan(fb!.x);
    expect(tb!.width).toBeGreaterThanOrEqual(44);
    expect(tb!.height).toBeGreaterThanOrEqual(44);
    const title = r.page.locator('#folders [data-feed="long"] .tree-title');
    const dimensions = await title.evaluate(n=>({height:n.getBoundingClientRect().height,line:Number.parseFloat(getComputedStyle(n).lineHeight)}));
    expect(dimensions.height).toBeGreaterThan(dimensions.line);
    expect(dimensions.height).toBeLessThanOrEqual(dimensions.line*2+.5);
    expect(await r.page.locator('#reading-nav').evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true);
    await folder.click();
    await toggle.focus(); await r.page.keyboard.press('Enter');
    await expect(folder).toHaveAttribute('aria-pressed','true');
    await expect(r.page.locator('#folders [data-feed="long"]')).toBeHidden();
    await expect(r.page.locator('#folders').getByRole('button',{name:'Раскрыть Наука',exact:true})).toBeFocused();
    await expect(r.page.locator('#folders').getByRole('button',{name:'Свернуть Физика',exact:true})).toHaveAttribute('aria-expanded','true');
    await r.page.locator('#folders').getByRole('button',{name:'Раскрыть Наука',exact:true}).click();
    await r.page.screenshot({path:artifactPath('feed-tree-ios-reading-mobile.png')});
    await r.page.setViewportSize({width:1280,height:900});
    await r.page.screenshot({path:artifactPath('feed-tree-ios-reading-desktop.png')});
    await r.page.evaluate(()=>readerTransport.replies.set('/api/navigation',{body:{count:0,folders:[],feeds:[]}}));
    await expect(r.page.locator('#folders [data-folder]')).toHaveCount(0);
    await expect(r.page.locator('#all-folders')).toHaveAttribute('aria-pressed','true');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});


test('empty feed settings offer addition and catalog refresh reveals newly imported sources', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(()=>readerTransport.native.add('/api/subscriptions'));
    await r.page.locator('#feeds-open').click();
    await expect(r.page.locator('#feed-tree')).toHaveText('Фидов пока нет. Добавьте источник.');
    await r.page.locator('#feeds-close').click();
    await r.api('import','POST',{opml:'<opml><body><outline text="Учёба"><outline text="Новый источник" xmlUrl="https://source.test/new"/></outline></body></opml>'});
    await r.page.locator('#feeds-open').click();
    await expect(r.page.locator('#feed-tree').getByRole('button',{name:'Новый источник',exact:true})).toBeVisible();
    await r.page.locator('#feed-tree').getByRole('button',{name:'Свернуть Учёба',exact:true}).focus();
    await r.page.keyboard.press('Enter');
    await expect(r.page.locator('#feed-tree').getByRole('button',{name:'Раскрыть Учёба',exact:true})).toBeFocused();
    await expect(r.page.locator('#feed-tree').getByRole('button',{name:'Новый источник',exact:true})).toBeHidden();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('Back focuses the destination folder after moving the selected feed into a collapsed folder', async () => {
  const r = await reader();
  try {
    await r.page.setViewportSize({width:390,height:844});
    const data = await r.api('import','POST',{opml:'<opml><body><outline text="Наука"><outline text="Источник" xmlUrl="https://source.test/move"/></outline><outline text="Физика"/></body></opml>'});
    const destination = data.folders.find(folder=>folder.title==='Физика');
    await r.start([]);
    await r.page.evaluate(()=>readerTransport.native.add('/api/subscriptions'));
    await r.page.locator('#feeds-open').click();
    const tree = r.page.locator('#feed-tree');
    await tree.getByRole('button',{name:'Свернуть Физика',exact:true}).click();
    await tree.getByRole('button',{name:'Источник',exact:true}).click();
    await r.page.getByRole('button',{name:'Подключение и параметры',exact:true}).click();
    await r.page.locator('#feed-folder').selectOption(destination!.id);
    await r.page.getByRole('button',{name:'Сохранить фид',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Фид сохранён');
    await r.page.locator('#feeds-back').click();
    await expect(tree.getByRole('button',{name:'Источник',exact:true})).toBeHidden();
    await expect(tree.getByRole('button',{name:'Раскрыть Физика',exact:true})).toHaveAttribute('aria-expanded','false');
    await expect(tree.getByRole('button',{name:'Физика',exact:true})).toBeFocused();
    expect((await r.api('subscriptions')).feeds[0].folder_id).toBe(destination!.id);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
