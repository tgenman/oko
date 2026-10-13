const {test} = require('node:test');
const {reader, expect} = require('./support.cjs');

const models = [
  {model:'small',display_name:'Small',default:true,reasoning_efforts:['low','high'],default_reasoning_effort:'low',fast_supported:true},
  {model:'large',display_name:'Large',default:false,reasoning_efforts:['high'],default_reasoning_effort:'high',fast_supported:false},
];

test('Codex has four explicit choices; Fast starts off and unsupported effort is preserved visibly', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(models => {
      readerTransport.replies.set('/api/subscriptions', {body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/processors/codex', {body:{config:null}});
      readerTransport.replies.set('/api/processors/codex/models', {body:{models}});
      readerTransport.replies.set('/api/processors/llamacpp', {body:null});
    }, models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-choices fieldset')).toHaveCount(4);
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    await expect(r.page.locator('#codex-theses-fast')).not.toBeChecked();
    await r.page.locator('#codex-theses-fast').check();
    await r.page.locator('#codex-theses-model').selectOption('large');
    await expect(r.page.locator('#codex-theses-effort')).toHaveValue('low');
    await expect(r.page.locator('#codex-choice-status')).toContainText('не поддерживает');
    await expect(r.page.locator('#codex-save')).toBeDisabled();
    await r.page.locator('#codex-theses-effort').selectOption('high');
    await r.page.locator('#codex-theses-fast').uncheck();
    await r.page.locator('#codex-save').click();
    await expect(r.page.locator('#codex-choice-status')).toContainText('сохранены');
    const saved = await r.page.evaluate(() => readerTransport.requests.find(c=>c.url==='/api/processors/codex'&&c.method==='PUT'));
    expect(saved.body.theses).toEqual({model:'large',reasoning_effort:'high',fast:false});
    expect(saved.body.events).toEqual({model:'small',reasoning_effort:'low',fast:false});
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('late catalog and save replies keep new drafts across closing settings', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(models => {
      readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/processors/llamacpp',{body:null});
      readerTransport.replies.set('/api/processors/codex',{body:{config:null}});
      readerTransport.replies.set('/api/processors/codex/models',{body:{models}});
    }, models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    await r.page.evaluate(()=>readerTransport.held.set('/api/processors/codex',true));
    await r.page.locator('#codex-save').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/processors/codex'));
    await r.page.locator('#codex-theses-effort').selectOption('high');
    await r.page.keyboard.press('Escape');
    await r.page.evaluate(()=>readerTransport.release('/api/processors/codex',{}));
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-theses-effort')).toHaveValue('high');
    await r.page.evaluate(()=>readerTransport.held.set('/api/processors/codex/models',true));
    await r.page.locator('#codex-models-refresh').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/processors/codex/models'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await r.page.evaluate(()=>readerTransport.release('/api/processors/codex/models',{models:[]}));
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    await expect(r.page.locator('#codex-theses-effort')).toHaveValue('high');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('ready material displays old model and offers explicit recreation', async () => {
  const {material} = require('./support.cjs');
  const r = await reader();
  try {
    const source = material('one',{ai:{status:'done',error:null},theses:{points:[{text:'Тезис',sources:[{id:'one',url:'https://example.org/one'}]}]},provenance:{processor:'codex',model:'old-model',reasoning_effort:'high',fast:false}});
    await r.start([source]);
    await r.page.locator('#cards button').click();
    await r.page.locator('#processing-details summary').click();
    await expect(r.page.locator('#processing-provenance')).toContainText('old-model');
    await expect(r.page.locator('#processing-provenance')).toContainText('high');
    await expect(r.page.locator('#processing-provenance')).toContainText('Fast выключен');
    await r.page.evaluate(()=>readerTransport.replies.set('/api/materials/one/regenerate',{body:{ok:true}}));
    await r.page.locator('#regenerate').click();
    await r.page.waitForFunction(()=>readerTransport.requests.some(c=>c.url==='/api/materials/one/regenerate'&&c.method==='POST'));
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('catalog refresh before initial config response never replaces saved choices with defaults', async () => {
  const r=await reader();
  try {
    await r.start([]);
    const saved=Object.fromEntries(['filter','theses','events','digest'].map(kind=>[kind,{model:'large',reasoning_effort:'high',fast:false}]));
    await r.page.evaluate(models=>{
      readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/processors/llamacpp',{body:null});
      readerTransport.replies.set('/api/processors/codex/models',{body:{models}});
      readerTransport.held.set('/api/processors/codex',true);
    },models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/processors/codex'));
    await r.page.locator('#codex-models-refresh').click();
    await r.page.waitForFunction(()=>readerTransport.requests.some(r=>r.url==='/api/processors/codex/models'));
    await r.page.evaluate(config=>readerTransport.release('/api/processors/codex',{config}),saved);
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('large');
    await expect(r.page.locator('#codex-theses-effort')).toHaveValue('high');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('llama.cpp keeps connection drafts and concurrent save does not suppress availability results', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(models => {
      readerTransport.replies.set('/api/subscriptions', {body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/processors/codex', {body:{config:null}});
      readerTransport.replies.set('/api/processors/codex/models', {body:{models}});
      readerTransport.replies.set('/api/processors/llamacpp', {body:{endpoint:'http://127.0.0.1:8080',model:'old',local_execution:true}});
    }, models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await expect(r.page.locator('[data-settings-page="models"]')).toBeVisible();
    await r.page.locator('[data-settings-target="llamacpp"]').click();
    await expect(r.page.locator('#llamacpp-model')).toHaveValue('old');
    await r.page.locator('#llamacpp-model').fill('new');
    await expect(r.page.locator('#llamacpp-local')).not.toBeChecked();
    await r.page.locator('#llamacpp-local').check();
    await r.page.evaluate(()=>readerTransport.held.set('/api/processors/llamacpp/check',true));
    await r.page.locator('#llamacpp-check').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/processors/llamacpp/check'));
    await r.page.evaluate(()=>readerTransport.replies.set('/api/processors/llamacpp',{body:{ok:true}}));
    await r.page.locator('#llamacpp-save').click();
    await expect(r.page.locator('#llamacpp-status')).toContainText('сохранено');
    await r.page.evaluate(()=>readerTransport.release('/api/processors/llamacpp/check',{mode:'local',model:'new',models:['new']}));
    await expect(r.page.locator('#llamacpp-check-status')).toContainText('local');
    await r.page.locator('#llamacpp-model').fill('draft');
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="llamacpp"]').click();
    await expect(r.page.locator('#llamacpp-model')).toHaveValue('draft');
    const saved = await r.page.evaluate(()=>readerTransport.requests.find(c=>c.url==='/api/processors/llamacpp'&&c.method==='PUT'));
    expect(saved.body).toEqual({endpoint:'http://127.0.0.1:8080',model:'new',local_execution:true});
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('llama.cpp distinguishes newer unsaved input and ignores a check from a previous screen', async () => {
  const r=await reader();
  try {
    await r.start([]);
    await r.page.evaluate(models=>{
      readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/processors/codex',{body:{config:null}});
      readerTransport.replies.set('/api/processors/codex/models',{body:{models}});
      readerTransport.replies.set('/api/processors/llamacpp',{body:{endpoint:'http://127.0.0.1:8080',model:'old',local_execution:false}});
    },models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="llamacpp"]').click();
    await expect(r.page.locator('#llamacpp-model')).toHaveValue('old');
    await r.page.evaluate(()=>readerTransport.held.set('/api/processors/llamacpp',true));
    await r.page.locator('#llamacpp-save').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/processors/llamacpp'));
    await r.page.locator('#llamacpp-model').fill('new draft');
    await r.page.evaluate(()=>readerTransport.release('/api/processors/llamacpp',{}));
    await expect(r.page.locator('#llamacpp-status')).toContainText('Новый ввод ещё не сохранён');
    await expect(r.page.locator('#llamacpp-model')).toHaveValue('new draft');
    await r.page.evaluate(()=>readerTransport.held.set('/api/processors/llamacpp/check',true));
    await r.page.locator('#llamacpp-check').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/processors/llamacpp/check'));
    await r.page.locator('#settings-dialog .settings-back').click();
    await r.page.locator('[data-settings-target="llamacpp"]').click();
    await r.page.evaluate(()=>readerTransport.release('/api/processors/llamacpp/check',{mode:'obsolete',model:'old',models:['old']}));
    await expect(r.page.locator('#llamacpp-check-status')).not.toContainText('obsolete');
    await expect(r.page.locator('#llamacpp-model')).toHaveValue('new draft');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('Codex catalog error preserves configured choices and refresh recovers without replacement', async () => {
  const r=await reader();
  try {
    await r.start([]);
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/processors/llamacpp',{body:null});
      readerTransport.replies.set('/api/processors/codex',{body:{config:Object.fromEntries(['filter','theses','events','digest'].map(kind=>[kind,{model:'large',reasoning_effort:'high',fast:false}]))}});
      readerTransport.replies.set('/api/processors/codex/models',{status:503,body:{detail:'Каталог временно недоступен'}});
    });
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-choice-status')).toContainText('Каталог временно недоступен');
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('large');
    await expect(r.page.locator('#codex-save')).toBeDisabled();
    await r.page.evaluate(models=>readerTransport.replies.set('/api/processors/codex/models',{body:{models}}),models);
    await r.page.locator('#codex-models-refresh').click();
    await expect(r.page.locator('#codex-save')).toBeEnabled();
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('large');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('returning to Codex retries a catalog load interrupted by section navigation', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(models => {
      readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/processors/llamacpp',{body:null});
      readerTransport.replies.set('/api/processors/codex/models',{body:{models}});
      readerTransport.held.set('/api/processors/codex/models',true);
    }, models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/processors/codex/models'));
    await r.page.locator('#settings-dialog .settings-back').click();
    await r.page.evaluate(()=>{
      window.interruptedCatalogReply = readerTransport.pending.get('/api/processors/codex/models');
      readerTransport.pending.delete('/api/processors/codex/models');
      readerTransport.held.delete('/api/processors/codex/models');
    });
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    await r.page.evaluate(()=>window.interruptedCatalogReply(new Response(JSON.stringify({models:[]}),{headers:{'Content-Type':'application/json'}})));
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
