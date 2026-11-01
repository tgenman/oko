import {material, reader, expect} from './support.ts';
import {test} from 'node:test';
import {models} from './settings-fixtures.ts';


test('Codex has two explicit choices; Fast starts off and unsupported effort is preserved visibly', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(models => {
      readerTransport.replies.set('/api/v3/subscriptions', {body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/v3/processors/codex', {body:{config:null}});
      readerTransport.replies.set('/api/v3/processors/codex/models', {body:{models}});
      readerTransport.replies.set('/api/v3/processors/llamacpp', {body:null});
    }, models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-choices fieldset')).toHaveCount(2);
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    await expect(r.page.locator('#codex-theses-fast')).not.toBeChecked();
    await r.page.locator('#codex-theses-fast').check();
    await r.page.locator('#codex-theses-model').selectOption('large');
    await expect(r.page.locator('#codex-theses-effort')).toHaveValue('low');
    await expect(r.page.locator('#codex-choice-status')).toContainText('does not support');
    await expect(r.page.locator('#codex-save')).toBeDisabled();
    await r.page.locator('#codex-theses-effort').selectOption('high');
    await r.page.locator('#codex-theses-fast').uncheck();
    await r.page.locator('#codex-save').click();
    await expect(r.page.locator('#codex-choice-status')).toContainText('saved');
    const saved = await r.page.evaluate(() => readerTransport.requests.find(c=>c.url==='/api/v3/processors/codex'&&c.method==='PUT'));
    expect(saved!.body!.theses).toEqual({model:'large',reasoning_effort:'high',fast:false});
    expect(saved!.body!.filter).toEqual({model:'small',reasoning_effort:'low',fast:false});
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('late catalog and save replies keep new drafts across closing settings', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(models => {
      readerTransport.replies.set('/api/v3/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/v3/processors/llamacpp',{body:null});
      readerTransport.replies.set('/api/v3/processors/codex',{body:{config:null}});
      readerTransport.replies.set('/api/v3/processors/codex/models',{body:{models}});
    }, models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/processors/codex',true));
    await r.page.locator('#codex-save').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/processors/codex'));
    await r.page.locator('#codex-theses-effort').selectOption('high');
    await r.page.keyboard.press('Escape');
    await r.page.evaluate(()=>readerTransport.release('/api/v3/processors/codex',{}));
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-theses-effort')).toHaveValue('high');
    await r.page.evaluate(()=>readerTransport.held.set('/api/v3/processors/codex/models',true));
    await r.page.locator('#codex-models-refresh').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/processors/codex/models'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await r.page.evaluate(()=>readerTransport.release('/api/v3/processors/codex/models',{models:[]}));
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    await expect(r.page.locator('#codex-theses-effort')).toHaveValue('high');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('Codex catalog error preserves configured choices and refresh recovers without replacement', async () => {
  const r=await reader();
  try {
    await r.start([]);
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/v3/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/v3/processors/llamacpp',{body:null});
      readerTransport.replies.set('/api/v3/processors/codex',{body:{config:Object.fromEntries(['filter','theses'].map(kind=>[kind,{model:'large',reasoning_effort:'high',fast:false}]))}});
      readerTransport.replies.set('/api/v3/processors/codex/models',{status:503,body:{detail:'Каталог временно недоступен'}});
    });
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-choice-status')).toContainText('Каталог временно недоступен');
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('large');
    await expect(r.page.locator('#codex-save')).toBeDisabled();
    await r.page.evaluate(models=>readerTransport.replies.set('/api/v3/processors/codex/models',{body:{models}}),models);
    await r.page.locator('#codex-models-refresh').click();
    await expect(r.page.locator('#codex-save')).toBeEnabled();
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('large');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
