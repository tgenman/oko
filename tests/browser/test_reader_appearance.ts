import {test} from 'node:test';
import {reader, material, expect} from './support.ts';
import {artifactPath} from './artifacts.ts';

test('reader header keeps every action beside Oko and progress below on wide and narrow screens', async () => {
  const r = await reader();
  try {
    await r.page.setViewportSize({width:1440,height:900});
    await r.start([material('one',{title:'Кто быстрее?',snippet:'Три одинаковые бутылки скатываются с горки.',changed:true,feed_title:'GetAClass'})]);
    const stage = 'Подготовка тезисов · ' + 'Длинное описание текущей работы '.repeat(12);
    await r.page.evaluate(stage => {
      readerTransport.replies.get('/api/status')!.body!.stage = stage;
    }, stage);
    await expect(r.page.locator('#progress')).toContainText(stage);
    const wide = artifactPath('reader-header-wide.png');
    await r.page.screenshot({path:wide}); console.info('Header screenshot:',wide);
    const brand = await r.page.locator('header .brand').boundingBox();
    expect(await r.page.locator('#collect .reader-action-icon').evaluate(n=>getComputedStyle(n).backgroundColor)).toBe('rgb(52, 199, 89)');
    const actions = ['collect','add-open','feeds-open','settings-open','status-toggle','stop'];
    for (const id of actions) {
      const box = await r.page.locator('#'+id).boundingBox();
      expect(Math.abs(box!.y+box!.height/2-brand!.y-brand!.height/2)).toBeLessThan(1);
    }
    const progress = await r.page.locator('#progress').boundingBox();
    expect(progress!.y).toBeGreaterThan(brand!.y+brand!.height);
    expect(await r.page.locator('#progress').evaluate(n=>n.scrollHeight<=n.clientHeight)).toBe(true);
    await r.page.locator('#status-toggle').click();
    const header = await r.page.locator('header').boundingBox();
    const panel = await r.page.locator('#status-panel').boundingBox();
    expect(Math.abs(panel!.y-header!.y-header!.height)).toBeLessThan(1);
    const panelShot=artifactPath('reader-header-panel.png');
    await r.page.screenshot({path:panelShot}); console.info('Header screenshot:',panelShot);
    await r.page.keyboard.press('Escape');
    await expect(r.page.locator('#status-toggle')).toBeFocused();
    await r.page.setViewportSize({width:390,height:844});
    await r.page.locator('#collect').focus();
    for (const id of actions.slice(1)) {
      await r.page.keyboard.press('Tab');
      await expect(r.page.locator('#'+id)).toBeFocused();
      await expect.poll(async()=>{
        const box=await r.page.locator('#'+id).boundingBox();
        return box!.x>=0 && box!.x+box!.width<=391;
      },{message:'Focused action is visible: '+id}).toBe(true);
    }
    expect(await r.page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
    const narrow = artifactPath('reader-header-narrow.png');
    await r.page.screenshot({path:narrow}); console.info('Header screenshot:',narrow);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('reader card appearance preserves the compact layout, metadata and real previews', async () => {
  const r = await reader();
  try {
    await r.page.setViewportSize({width:1440,height:900});
    await r.page.addInitScript(()=>readerTransport.replies.set('/api/sample-preview',{
      mime:'image/png',base64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR9sAAAAASUVORK5CYII='}));
    await r.start([
      material('one',{title:'Кто быстрее?',snippet:'Три одинаковые бутылки скатываются с горки: одна заполнена водой, в другой вода и воздух.',changed:true,feed_title:'GetAClass — Физика в опытах и экспериментах',published:1802341800,images:['/api/sample-preview']}),
      material('two',{title:'We Finally Know More About Humanity’s First Civilization',snippet:'IT’S HERE! The 12,027 Calendar just dropped on the kurzgesagt shop.',changed:true,feed_title:'Kurzgesagt – In a Nutshell',published:1801991220}),
      material('three',{title:'AI Just Crossed the Terrifying Line — Now What?',snippet:'In July of 2026, 700 AI agents hacked the infrastructure of Hugging Face.',complete:false,changed:true,feed_title:'Kurzgesagt – In a Nutshell',published:1801731480}),
    ]);
    await r.page.locator('[data-card="one"]').click();
    await expect(r.page.locator('[data-card="one"] img')).toBeVisible();
    const row = r.page.locator('[data-card="one"]');
    const preview = await row.locator('.card-preview').boundingBox();
    const text = await row.locator('.card-body').boundingBox();
    expect(preview!.width).toBe(76); expect(preview!.height).toBe(76);
    expect(text!.x-preview!.x-preview!.width).toBe(12);
    expect((await r.page.locator('#material-list').boundingBox())!.width).toBe(440);
    const geometry = await row.evaluate(n=>{
      const style=getComputedStyle(n),title=getComputedStyle(n.querySelector('.card-title')!),snippet=getComputedStyle(n.querySelector('.card-snippet')!);
      return {padding:style.padding,minHeight:style.minHeight,radius:style.borderRadius,margin:style.margin,
        titleSize:title.fontSize,titleLines:title.webkitLineClamp,snippetSize:snippet.fontSize,snippetLines:snippet.webkitLineClamp};
    });
    expect(geometry).toEqual({padding:'16px',minHeight:'112px',radius:'0px',margin:'0px',titleSize:'15px',titleLines:'2',snippetSize:'14px',snippetLines:'2'});
    await expect(row.locator('.card-kind')).toHaveText('Изменено');
    await expect(r.page.locator('[data-card="three"] .card-kind')).toHaveText('Изменено · Загружается');
    await expect(row.locator('.card-source')).toHaveText('GetAClass — Физика в опытах и экспериментах');
    await expect(row.locator('.card-date')).not.toHaveText('');
    await expect(r.page.locator('.date-group')).toHaveCount(3);
    const shot=artifactPath('reader-cards.png');
    await r.page.screenshot({path:shot}); console.info('Cards screenshot:',shot);
    expect(await row.evaluate(n=>getComputedStyle(n).backgroundColor)).toBe('rgb(231, 241, 255)');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
