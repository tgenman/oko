import {addSources} from './connections';
import {test} from 'node:test';
import {reader, expect} from './support.ts';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), 'oko-feed-tree-'));
const artifactPath = (name: string) => path.join(screenshots, name);
console.log('Screenshots:', screenshots);

test('feed settings independently expand folders and return to the selected object after catalog refresh', async () => {
  const r = await reader();
  try {
    await r.page.setViewportSize({width:1280,height:900});
    const data = await addSources(r.api, [["https://source.test/a", "Альфа — длинное название источника для двух строк", "Наука"], ["https://source.test/b", "Бета", "Наука"], ["https://source.test/c", "Гамма", "Физика"]]);
    await r.start([]);
    await r.page.evaluate(() => readerTransport.native.add('/api/v3/subscriptions'));
    await r.page.locator('#feeds-open').click();
    const tree = r.page.locator('#feed-tree');
    const science = tree.getByRole('button',{name:'Collapse Наука',exact:true});
    await science.click();
    await expect(tree.getByRole('button',{name:'Бета',exact:true})).toBeHidden();
    await expect(r.page.locator('#feed-list')).toBeVisible();
    await expect(tree.getByRole('button',{name:'Collapse Физика',exact:true})).toHaveAttribute('aria-expanded','true');
    await tree.getByRole('button',{name:'Expand Наука',exact:true}).click();
    const alpha = tree.getByRole('button',{name:'Альфа — длинное название источника для двух строк',exact:true});
    await alpha.click();
    await expect(r.page.locator('#owner-heading')).toHaveText('Feed: Альфа — длинное название источника для двух строк');
    await tree.getByRole('button',{name:'Collapse Физика',exact:true}).click();
    await r.page.locator('#feeds-close').click();
    await r.page.locator('#feeds-open').click();
    // The refreshed catalog retains independent expansion and alphabetical ordering.
    await expect(tree.getByRole('button',{name:'Expand Физика',exact:true})).toHaveAttribute('aria-expanded','false');
    expect(await tree.locator('[data-owner^="feed/"]').evaluateAll(ns=>ns.map(n=>n.getAttribute('title')))).toEqual(['Альфа — длинное название источника для двух строк','Бета','Гамма']);
    expect(await r.page.locator('#feeds-dialog').evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true);
    expect((await r.api('subscriptions')).feeds).toHaveLength(data.feeds.length);
    expect(await r.page.evaluate(()=>readerTransport.requests.filter(q=>['PATCH','PUT','POST','DELETE'].includes(q.method) && !q.url.includes('/tabs/') && !/^\/api\/v3\/client-sessions(?:\/[^/?]+)?$/.test(q.url) && q.url !== '/api/v3/diagnostics'))).toEqual([]);
    await r.page.setViewportSize({width:1280,height:900});
    await r.page.screenshot({path:artifactPath('feed-tree-settings-desktop.png')});
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
