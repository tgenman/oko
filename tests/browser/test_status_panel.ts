import {openAdmin,openReading} from './admin-navigation';
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
    await openAdmin(r.page);await r.page.locator('#status-toggle').click();
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
    await openReading(r.page);await r.page.locator('#settings-open').click();
    await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','home');
    await r.page.evaluate(()=>window.releaseOldMenu());
    await r.page.waitForTimeout(150);
    await expect(r.page.locator('#settings-dialog')).toHaveAttribute('data-settings-view','home');
  } finally {await r.close();}
});

test('offline state keeps last problems without a false zero and distinguishes a confirmed stop',async()=>{
  const r=await reader();
  try {
    await r.start([]);await status(r,[modelProblem]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await openAdmin(r.page);await r.page.locator('#status-toggle').click();
    await expect(r.page.locator('.status-problem')).toHaveCount(1);
    await r.page.evaluate(()=>readerTransport.replies.set('/api/v3/status',{status:503,body:{detail:'offline'}}));
    await expect(r.page.locator('#status-panel')).toContainText('Status may be outdated');
    await expect(r.page.locator('#status-panel')).toContainText('No Codex Model Selected');
    await expect(r.page.locator('.status-action')).toBeDisabled();
    await status(r,[modelProblem]);
    await expect(r.page.locator('.status-action')).toBeEnabled();
    await r.page.locator('#status-close').click();
    await expect(r.page.locator('#status-panel')).toBeHidden();
    await r.page.evaluate(()=>readerTransport.replies.get('/api/v3/status')!.body!.stopped=true);
    await expect(r.page.locator('#collection-progress')).toContainText('Oko stopped');
    await openAdmin(r.page);await r.page.locator('#status-toggle').click();
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
    await openAdmin(r.page);await r.page.locator('#status-toggle').click();
    const summary=r.page.locator('.status-details summary');await summary.click();
    await status(r,[],[{...modelProblem,state:'finished',finished_at:1800000010,outcome:'resolved'}]);
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 0');
    await expect(summary).toBeFocused();
    await expect(r.page.locator('.status-group .status-finished')).toHaveCount(1);
    await summary.click();
    await expect(r.page.locator('.status-group .status-finished')).toHaveCount(0);
    await expect(r.page.locator('.status-recent')).toBeVisible();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('viewing in another tab preserves problem references',async()=>{
  const r=await reader();
  try {
    await r.start([material('one')]);const other=await r.context.newPage();await r.start([material('one')],other);
    const problem: Problem={...modelProblem,id:'source',code:'material-incomplete',section:'object',objects:[{kind:'material',id:'one',label:'Заголовок удаляемого материала'}],action:{kind:'material',id:'one'}};
    await status(r,[problem]);await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');await openAdmin(r.page);await r.page.locator('#status-toggle').click();await r.page.locator('.status-details summary').click();
    await openReading(other);await other.locator('#cards button').click();
    await other.evaluate(()=>readerTransport.replies.set('POST /api/v3/materials/one/viewed',{body:{ok:true},next:[['/api/v3/cards',{body:window.readerFixtures.cardPage([])}]]}));
    await other.getByRole('button',{name:'Mark as Read',exact:true}).click();
    await expect(r.page.locator('#status-toggle')).toHaveText('Problems · 1');
    await expect(r.page.locator('.status-object')).toContainText('one');
    await status(r,[problem]);
    await expect(r.page.locator('.status-object')).toHaveCount(1);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
