import type {Problem} from './scenario-data';
import type {ReaderFixture} from './support.ts';
import {test} from 'node:test';
import {reader, material, expect} from './support.ts';
import {artifactPath} from './artifacts.ts';

const modelProblem: Problem = {id:'processor:codex-choice',code:'codex-choice',section:'action',title:'No Codex Model Selected',
  consequence:'Тезисы пока не создаются.',next_step:'Выберите модель.',objects:[{kind:'settings',id:'global',label:'Oko Settings'}],
  action:{kind:'settings',page:'codex'},created_at:1800000000,next_at:null,state:'current',finished_at:null,outcome:null};
async function status(r: ReaderFixture,current: Problem[],recent: Problem[]=[]) {
  await r.page.evaluate(({current,recent})=>readerTransport.replies.set('/api/v3/status',{body:{stage:'Готово',errors:[],sources:[],ready_sources:1,total_sources:1,
    problems:{current,recent,count:current.length,updated_at:1800000000}}}),{current,recent});
}

test('late status-target opening cannot navigate a reopened settings menu',async()=>{
  const r=await reader();
  try {
    await r.start([]);await status(r,[modelProblem]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await r.page.locator('#status-toggle').click();
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/subscriptions',true));
    await r.page.getByRole('button',{name:'Configure Model',exact:true}).click();
    await expect.poll(()=>r.page.evaluate(()=>readerTransport.pending.has('/api/v3/subscriptions'))).toBe(true);
    await r.page.locator('#settings-close').click();
    await r.page.evaluate(()=>{
      const resolve=readerTransport.pending.get('/api/v3/subscriptions');
      window.releaseOldMenu=()=>resolve!(new Response(JSON.stringify({folders:[],feeds:[]}),{headers:{'Content-Type':'application/json'}}));
      readerTransport.pending.delete('/api/v3/subscriptions');readerTransport.held.delete('/api/v3/subscriptions');
    });
    await r.page.locator('#status-close').click();
    await r.page.locator('#settings-open').click();
    await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','home');
    await r.page.evaluate(()=>window.releaseOldMenu());
    await r.page.waitForTimeout(150);
    await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','home');
  } finally {await r.close();}
});

test('status-target continuation respects a newer page in the same menu',async()=>{
  const r=await reader();
  try {
    await r.start([]);await status(r,[modelProblem]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await r.page.locator('#status-toggle').click();
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/subscriptions',true));
    await r.page.getByRole('button',{name:'Configure Model',exact:true}).click();
    await expect.poll(()=>r.page.evaluate(()=>readerTransport.pending.has('/api/v3/subscriptions'))).toBe(true);
    await r.page.getByRole('button',{name:'Models',exact:true}).click();
    await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','models');
    await r.page.evaluate(()=>readerTransport.release('/api/v3/subscriptions',{folders:[],feeds:[]}));
    await r.page.waitForTimeout(150);
    await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','models');
  } finally {await r.close();}
});

test('missing model opens the selected panel without moving reading and navigates to Codex',async()=>{
  const r=await reader();
  try {
    await r.start([material('one',{text:'Текст '.repeat(1000)})]);
    await status(r,[modelProblem]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await expect(r.page.locator('#status-panel')).toBeHidden();
    await r.page.locator('#cards button').click();
    await r.page.locator('article').evaluate(node=>node.scrollTop=200);
    const scroll=await r.page.locator('article').evaluate(node=>node.scrollTop);
    await r.page.locator('#status-toggle').click();
    await expect(r.page.locator('#status-panel')).toBeVisible();
    await expect(r.page.locator('#status-panel')).toContainText('No Codex Model Selected');
    await expect(r.page.locator('#title')).toHaveText('one');
    expect(await r.page.locator('article').evaluate(node=>node.scrollTop)).toBe(scroll);
    const desktop = artifactPath('status-panel-desktop.png');
    await r.page.screenshot({path:desktop}); console.info('Status panel screenshot:',desktop);
    await r.page.getByRole('button',{name:'Configure Model',exact:true}).click();
    await expect(r.page.locator('#settings-dialog')).toBeVisible();
    await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','codex');
    await r.page.locator('#settings-close').click();
    await expect(r.page.locator('#status-panel')).toBeVisible();
    await status(r,[]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Status');
    await expect(r.page.locator('#status-panel')).toContainText('All Clear');
    await r.page.locator('#status-close').click();
    await expect(r.page.locator('#status-toggle')).toBeFocused();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('late failed settings write is visible once in the panel and explicit draft discard completes it',async()=>{
  const r=await reader();
  try {
    await r.start([]);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="reading"]').click();
    await r.page.locator('[data-settings-target="field/theses"]').click();
    await r.page.locator('#setting-theses').uncheck();
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/settings/oko/global',true));
    await r.page.locator('#setting-save-theses').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/settings/oko/global'));
    await r.page.locator('#settings-close').click();
    await r.page.evaluate(()=>readerTransport.release('/api/v3/settings/oko/global',{detail:'Сохранение не удалось'},500));
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await expect(r.page.locator('#status-panel')).toBeHidden();
    await r.page.locator('#status-toggle').click();
    await expect(r.page.locator('.status-problem')).toHaveCount(1);
    await r.page.locator('.status-action').click();
    await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','field/theses');
    await expect(r.page.locator('#setting-theses')).not.toBeChecked();
    await r.page.locator('#setting-discard-theses').click();
    await r.page.locator('#settings-close').click();
    await expect(r.page.locator('#status-toggle')).toHaveText('Status');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('offline state keeps last problems without a false zero and distinguishes a confirmed stop',async()=>{
  const r=await reader();
  try {
    await r.start([]);await status(r,[modelProblem]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await r.page.locator('#status-toggle').click();
    await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/status',{status:503,body:{detail:'offline'}}));
    await expect(r.page.locator('#status-panel')).toContainText('Status may be outdated');
    await expect(r.page.locator('#status-panel')).toContainText('No Codex Model Selected');
    await expect(r.page.locator('.status-action')).toBeDisabled();
    await status(r,[modelProblem]);
    await expect(r.page.locator('.status-action')).toBeEnabled();
    await r.page.locator('#status-close').click();
    await expect(r.page.locator('#status-panel')).toBeHidden();
    await r.page.evaluate(()=>readerTransport.replies.get('/api/v3/status')!.body!.stopped=true);
    await expect(r.page.locator('#progress')).toHaveText('Oko stopped');
    await r.page.locator('#status-toggle').click();
    await expect(r.page.locator('#status-panel')).toContainText('Oko stopped');
    await expect(r.page.locator('.status-action')).toBeDisabled();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('expanded completion stays in place until collapsed and new problems do not replace focus',async()=>{
  const r=await reader();
  try {
    await r.start([]);await status(r,[modelProblem]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await r.page.locator('#status-toggle').click();
    const summary=r.page.locator('.status-details summary');await summary.click();
    await status(r,[],[{...modelProblem,state:'finished',finished_at:1800000010,outcome:'resolved'}]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Status');
    await expect(summary).toBeFocused();
    await expect(r.page.locator('.status-group .status-finished')).toHaveCount(1);
    await summary.click();
    await expect(r.page.locator('.status-group .status-finished')).toHaveCount(0);
    await expect(r.page.locator('.status-recent')).toBeVisible();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('panel reorders only on reopening and remains a nonmodal desktop sidebar',async()=>{
  const r=await reader();
  try {
    await r.start([]); await status(r,[modelProblem]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await r.page.locator('#status-toggle').click();
    const newer={...modelProblem,id:'newer',title:'Новая проблема',created_at:1800000010};
    await status(r,[newer,modelProblem]);
    await expect(r.page.locator('.status-problem')).toHaveCount(2);
    await expect(r.page.locator('.status-problem').first()).toHaveAttribute('data-problem-id',modelProblem.id);
    await r.page.locator('#status-close').click();await r.page.locator('#status-toggle').click();
    await expect(r.page.locator('.status-problem').first()).toHaveAttribute('data-problem-id',newer.id);
    await expect(r.page.locator('#status-panel')).toHaveAttribute('role','complementary');
    await expect(r.page.locator('#status-panel')).not.toHaveAttribute('aria-modal','true');
    await r.page.keyboard.press('Escape');
    await expect(r.page.locator('#status-toggle')).toBeFocused();
    expect(await r.page.locator('#reader-shell').evaluate(n=>(n as HTMLElement).inert)).toBe(false);
  } finally {await r.close();}
});

test('discarding a failed processor draft restores its saved values',async()=>{
  const r=await reader();
  try {
    await r.start([]);
    await r.page.evaluate(()=>{
      const choice={model:'small',reasoning_effort:'low',fast:false};
      readerTransport.replies.set('/api/v3/processors/codex',{body:{config:Object.fromEntries(['filter','theses'].map(k=>[k,choice]))}});
      readerTransport.replies.set('/api/v3/processors/codex/models',{body:{models:[{model:'small',display_name:'Small',reasoning_efforts:['low','high'],default_reasoning_effort:'low',fast_supported:false,default:true}]}});
    });
    await r.page.locator('#settings-open').click();await r.page.locator('[data-settings-target="models"]').click();await r.page.locator('[data-settings-target="codex"]').click();
    await r.page.locator('#codex-theses-effort').selectOption('high');
    await r.page.evaluate(()=>readerTransport.replies.set('PUT /api/v3/processors/codex',{status:500,body:{detail:'synthetic failure'}}));
    await r.page.locator('#codex-save').click();
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await r.page.locator('#codex-discard').click();
    await expect(r.page.locator('#codex-theses-effort')).toHaveValue('low');
    await expect(r.page.locator('#status-toggle')).toHaveText('Status');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('adding an affected object preserves keyboard focus in expanded details',async()=>{
  const r=await reader();
  try {
    await r.start([material('one'),material('two')]);
    const problem: Problem={...modelProblem,id:'source',code:'source-connection',section:'object',objects:[{kind:'material',id:'one',label:'Первый материал'}],action:{kind:'objects'}};
    await status(r,[problem]);await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');await r.page.locator('#status-toggle').click();
    await r.page.locator('.status-details summary').click();await r.page.locator('.status-object').focus();
    await status(r,[{...problem,objects:[{kind:'material',id:'two',label:'Второй материал'},...problem.objects]}]);
    await expect(r.page.locator('.status-object')).toHaveCount(2);
    await expect(r.page.locator('[data-resource-key="material:one"]')).toBeFocused();
  } finally {await r.close();}
});

test('viewing in another tab removes expanded and stale problem references',async()=>{
  const r=await reader();
  try {
    await r.start([material('one')]);const other=await r.context.newPage();await r.start([material('one')],other);
    const problem: Problem={...modelProblem,id:'source',code:'material-incomplete',section:'object',objects:[{kind:'material',id:'one',label:'Заголовок удаляемого материала'}],action:{kind:'material',id:'one'}};
    await status(r,[problem]);await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');await r.page.locator('#status-toggle').click();await r.page.locator('.status-details summary').click();
    await other.locator('#cards button').click();
    await other.evaluate(()=>readerTransport.replies.set('POST /api/v3/materials/one/viewed',{body:{ok:true},next:[['/api/v3/cards',{body:[]}]]}));
    await other.getByRole('button',{name:'Mark as Read',exact:true}).click();
    await expect(r.page.locator('#status-toggle')).toHaveText('Status');
    await expect(r.page.locator('#status-panel')).not.toContainText('Заголовок удаляемого материала');
    await status(r,[problem]);
    await expect(r.page.locator('.status-object')).toHaveCount(0);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
