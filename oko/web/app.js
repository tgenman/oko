'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const tab = crypto.randomUUID();
const byId = id => document.getElementById(id);
const bus = new BroadcastChannel('oko-reader');
let selected = null;
let generation = 0;
let contentEpoch = 0;
let stopped = false;
let ids = new Set();
let folderId = null;
let refreshTimer = null;
async function api(path, method = 'GET', body = undefined) {
  const response = await fetch('/api/' + path, {method, cache: 'no-store', headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(typeof result.detail === 'string' ? result.detail : response.status === 422 ? 'Проверьте значения настроек' : 'Связь с Око потеряна');
    error.detail = result.detail;
    throw error;
  }
  return result;
}
function clear() {
  generation++;
  selected = null;
  byId('material').hidden = true;
  byId('empty').hidden = false;
  ['title', 'text', 'date', 'message'].forEach(id => { byId(id).textContent = ''; });
}
function stopUi(message) {
  stopped = true;
  contentEpoch++;
  clear();
  byId('cards').replaceChildren();
  byId('folders').replaceChildren();
  byId('count').textContent = '0';
  byId('progress').textContent = message;
  byId('stop').disabled = true;
  byId('settings-dialog').close();
  byId('settings-open').disabled = true;
}
bus.onmessage = event => {
  if (event.data === 'stop') stopUi('Око остановлено');
  else {
    contentEpoch++;
    generation++;
    ids.delete(event.data);
    if (selected && event.data === selected.id) clear();
  }
};
async function open(id) {
  const stamp = ++generation;
  try {
    const material = await api('materials/' + id);
    if (stamp !== generation || !ids.has(id) || stopped) return;
    selected = material;
    byId('title').textContent = material.title;
    byId('text').textContent = material.text;
    byId('completeness').textContent = material.complete ? 'Тестовый источник · Полностью сохранено' : 'Загрузка материала';
    byId('date').textContent = new Date(material.published * 1000).toLocaleString('ru');
    byId('message').textContent = '';
    byId('material').hidden = false;
    byId('empty').hidden = true;
  } catch (error) { clear(); byId('progress').textContent = error.message; }
}
async function refresh() {
  clearTimeout(refreshTimer);
  if (stopped) return;
  const epoch = contentEpoch;
  try {
    const state = await api('status');
    if (stopped || epoch !== contentEpoch) return;
    if (state.stopped) { stopUi('Око остановлено'); return; }
    byId('progress').textContent = state.error || `${state.stage} · источники ${state.ready_sources}/${state.total_sources}` + (state.errors.length ? ' · ' + state.errors.join(', ') : '');
    if (state.error) { clear(); return; }
    const params = new URLSearchParams({sort: byId('sort').value});
    if (folderId) params.set('folder', folderId);
    const cards = await api('cards?' + params);
    if (stopped || epoch !== contentEpoch) return;
    ids = new Set(cards.map(card => card.id));
    if (selected && !ids.has(selected.id)) clear();
    if (selected && cards.some(card => card.id === selected.id && card.complete !== selected.complete)) await open(selected.id);
    byId('count').textContent = cards.length;
    byId('cards').replaceChildren(...cards.map(card => {
      const button = document.createElement('button');
      button.classList.toggle('active', selected?.id === card.id);
      button.textContent = card.title;
      const date = document.createElement('small');
      date.textContent = new Date(card.published * 1000).toLocaleString('ru');
      button.append(date);
      button.onclick = () => open(card.id);
      return button;
    }));
    const folders = await api('folders');
    if (stopped || epoch !== contentEpoch) return;
    byId('folders').replaceChildren(...folders.map(folder => {
      const button = document.createElement('button');
      button.textContent = `${folder.title} · ${folder.count}`;
      button.classList.toggle('active', folder.id === folderId);
      button.onclick = () => { folderId = folder.id; contentEpoch++; clear(); refresh(); };
      return button;
    }));
  } catch (error) { clear(); byId('progress').textContent = error.message; }
  finally { if (!stopped) refreshTimer = setTimeout(refresh, 500); }
}
byId('copy').onclick = async () => {
  if (!selected) return;
  try {
    await navigator.clipboard.writeText(selected.url);
    byId('message').textContent = 'Ссылка скопирована';
  } catch { byId('message').textContent = 'Браузер не разрешил копирование'; }
};
byId('viewed').onclick = async () => {
  if (!selected) return;
  const id = selected.id;
  try {
    await api('materials/' + id + '/viewed', 'POST');
    contentEpoch++;
    ids.delete(id);
    bus.postMessage(id);
    clear();
  } catch (error) { byId('message').textContent = error.message; }
};
byId('stop').onclick = async () => {
  try { await api('stop', 'POST'); bus.postMessage('stop'); stopUi('Око остановлено'); }
  catch (error) { byId('progress').textContent = error.message; }
};
async function heartbeat() {
  if (stopped) return;
  try { await api('tabs/' + tab + '/heartbeat', 'POST'); }
  catch (error) { clear(); byId('progress').textContent = error.message; }
  if (!stopped) setTimeout(heartbeat, 2000);
}
window.addEventListener('pagehide', () => {
  fetch('/api/tabs/' + tab + '/close', {method:'POST', keepalive:true, headers:{'Authorization':'Bearer '+token}}).catch(() => {});
  clear();
});
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
byId('all-folders').onclick = () => { folderId = null; contentEpoch++; clear(); refresh(); };
byId('sort').onchange = () => { contentEpoch++; refresh(); };
heartbeat().then(refresh);

const fields = {
  originals: ['Оригиналы', 'boolean'], theses: ['Тезисы публикаций', 'boolean'],
  digests: ['Обзоры за период', 'boolean'], prompt_theses: ['Промпт тезисов', 'text'],
  prompt_digest: ['Промпт обзора', 'text'], prompt_filters: ['Правила фильтрации (промпт)', 'text'],
  schedule: ['Расписание обзора (off или ЧЧ:ММ, местное время)', 'string'],
  digest_days: ['Период обзора, дней', 'number'], processor: ['Обработчик (например codex или ollama:модель)', 'string']
};
const importanceNames = {normal: 'Обычная', elevated: 'Повышенная', high: 'Высокая'};
let subscriptions = {folders: [], feeds: []};
let importText = null;
let importChoices = {};
let settingsGeneration = 0;
function element(tag, text = '') { const node = document.createElement(tag); node.textContent = text; return node; }
function selectControl(options, value) {
  const select = document.createElement('select');
  for (const [id, title] of options) { const option = element('option', title); option.value = id; select.append(option); }
  select.value = String(value);
  return select;
}
function labelled(title, input) { const label = element('label', title); label.append(input); return label; }
function scopePath() { return byId('settings-owner').value || 'oko/global'; }
function describeOwner(scope, owner) {
  if (scope === 'default') return 'Око · начальное значение';
  if (scope === 'oko') return 'Око';
  const list = scope === 'folder' ? subscriptions.folders : subscriptions.feeds;
  return (scope === 'folder' ? 'Папка: ' : 'Фид: ') + (list.find(item => item.id === owner)?.title || owner);
}
async function loadSubscriptions(preferred = scopePath()) {
  subscriptions = await api('subscriptions');
  const owner = byId('settings-owner');
  owner.replaceChildren();
  const options = [['oko/global', 'Око'], ...subscriptions.folders.map(f => ['folder/' + f.id, 'Папка: ' + f.title]), ...subscriptions.feeds.map(f => ['feed/' + f.id, 'Фид: ' + f.title])];
  for (const [id, title] of options) { const option = element('option', title); option.value = id; owner.append(option); }
  owner.value = options.some(([id]) => id === preferred) ? preferred : 'oko/global';
  await loadSettings();
}
async function loadSettings() {
  const stamp = ++settingsGeneration;
  const path = scopePath();
  try {
    const result = await api('settings/' + path);
    if (stamp !== settingsGeneration || stopped) return;
    byId('feed-options').replaceChildren();
    const feed = subscriptions.feeds.find(f => 'feed/' + f.id === path);
    if (feed) renderFeed(feed);
    const container = byId('setting-fields');
    container.replaceChildren();
    for (const [field, [title, type]] of Object.entries(fields)) {
      const effective = result.effective[field];
      const row = element('div'); row.className = 'setting-row';
      let input;
      if (type === 'boolean') input = selectControl([['true', 'Включено'], ['false', 'Выключено']], effective.value);
      else if (type === 'text') { input = element('textarea'); input.value = effective.value; input.rows = 3; }
      else { input = element('input'); input.type = type === 'number' ? 'number' : 'text'; input.value = effective.value; if (type === 'number') { input.min = '1'; input.max = '3650'; } }
      input.id = 'setting-' + field;
      const info = element('small', 'Эффективное значение: ' + String(effective.value) + ' · ' + describeOwner(effective.scope, effective.owner));
      const save = element('button', 'Сохранить поле');
      const reset = element('button', 'Сбросить');
      reset.disabled = !(field in result.overrides);
      async function update(value) {
        try {
          await api('settings/' + path, 'PATCH', {[field]: value});
          if (path === scopePath()) await loadSettings();
          byId('settings-message').textContent = 'Настройка сохранена';
        } catch (error) { byId('settings-message').textContent = error.message; }
      }
      save.onclick = () => update(type === 'boolean' ? input.value === 'true' : type === 'number' ? Number(input.value) : input.value);
      reset.onclick = () => update(null);
      row.append(labelled(title, input), info, save, reset); container.append(row);
    }
  } catch (error) { byId('settings-message').textContent = error.message; }
}
function renderFeed(feed) {
  const container = byId('feed-options');
  container.append(element('h2', 'Подключение фида'));
  const connection = feed.kind === 'demo' ? 'Тестовый источник подключён' : feed.connection === 'ambiguous' ? 'Адрес неоднозначен: требуется явный выбор подключения' : 'Требуется подключение сборщика';
  container.append(element('p', connection), element('p', 'Исходные адреса: ' + feed.original_urls.join(', ')));
  const folder = selectControl(subscriptions.folders.map(f => [f.id, f.title]), feed.folder_id);
  const importance = selectControl(Object.entries(importanceNames), feed.importance);
  const days = element('input'); days.type = 'number'; days.min = '1'; days.max = '3650'; days.value = feed.initial_days;
  const publicAi = element('input'); publicAi.type = 'checkbox'; publicAi.checked = feed.public_ai;
  container.append(labelled('Папка', folder), labelled('Важность фида', importance), labelled('Первоначальный период, дней', days), labelled('Разрешить обработку публичным AI', publicAi));
  let auto, history, transport;
  if (feed.kind === 'telegram') {
    auto = selectControl([['', 'Выберите режим'], ['true', 'Автопрочитывать после полного сохранения'], ['false', 'Отмечать только после «Просмотрено»']], feed.auto_read === null ? '' : feed.auto_read);
    history = element('input'); history.type = 'checkbox'; history.checked = feed.read_history;
    container.append(labelled('Автопрочитывание в Telegram', auto), labelled('Отметить историю до начала импорта прочитанной', history), element('p', 'Без разрешения очистки старая непрочитанная история может задержать отметки Telegram. Сохранение настроек не отправляет отметки.'));
  }
  if (feed.kind === 'telegram' || feed.kind === 'unresolved') {
    transport = selectControl([['', 'Подключение не выбрано'], ...(feed.kind === 'telegram' ? [['telegram', 'Telegram (требуется авторизация)']] : []), ['rss', 'Ограниченный RSS (без Telegram-медиа и отметок чтения)']], feed.transport || '');
    container.append(labelled('Способ сбора', transport));
  }
  const save = element('button', 'Сохранить фид');
  save.onclick = async () => {
    const patch = {folder_id: folder.value, importance: importance.value, initial_days: Number(days.value), public_ai: publicAi.checked};
    if (auto) { patch.auto_read = auto.value === '' ? null : auto.value === 'true'; patch.read_history = history.checked; }
    if (transport) patch.transport = transport.value || null;
    try {
      await api('feeds/' + feed.id, 'PATCH', patch);
      await loadSubscriptions('feed/' + feed.id);
      contentEpoch++; refresh();
      byId('settings-message').textContent = 'Фид сохранён';
    } catch (error) { byId('settings-message').textContent = error.message; }
  };
  container.append(save);
}
function showConflicts(conflicts) {
  const container = byId('import-plan');
  for (const conflict of conflicts) {
    const select = selectControl([['', 'Выберите одну папку'], ...conflict.folders.map(f => [f, f])], importChoices[conflict.identity] || '');
    select.onchange = () => { importChoices[conflict.identity] = select.value; };
    container.append(labelled(conflict.identity + ' встречается в нескольких папках', select));
  }
}
byId('settings-open').onclick = async () => {
  byId('settings-dialog').showModal();
  byId('settings-message').textContent = '';
  try { await loadSubscriptions(); } catch (error) { byId('settings-message').textContent = error.message; }
};
byId('settings-close').onclick = () => byId('settings-dialog').close();
byId('settings-owner').onchange = loadSettings;
byId('opml-file').onchange = async () => {
  const file = byId('opml-file').files[0];
  importText = null; importChoices = {}; byId('import-confirm').hidden = true;
  if (!file) return;
  try {
    if (file.size > 2000000) throw new Error('OPML больше 2 МБ');
    importText = await file.text();
    const plan = await api('import/preview', 'POST', {opml: importText});
    byId('import-plan').replaceChildren(element('p', `Папок: ${plan.folders.length}; источников: ${plan.feeds.length}. Повторяющиеся адреса объединены. Новые фиды: сутки истории, публичный AI и очистка истории выключены.`));
    showConflicts(plan.feeds.filter(f => f.folders.length > 1));
    byId('import-confirm').hidden = false;
  } catch (error) { importText = null; byId('settings-message').textContent = error.message; }
};
byId('import-confirm').onclick = async () => {
  if (!importText) return;
  try {
    await api('import', 'POST', {opml: importText, choices: importChoices});
    importText = null; importChoices = {}; byId('opml-file').value = ''; byId('import-confirm').hidden = true;
    byId('import-plan').replaceChildren(element('p', 'Подписки импортированы. Выберите фид для настройки подключения.'));
    await loadSubscriptions(); contentEpoch++; refresh();
    byId('settings-message').textContent = 'Импорт завершён';
  } catch (error) {
    if (error.detail?.conflicts) { byId('import-plan').replaceChildren(element('p', 'Выберите одну папку для каждого конфликта и повторите импорт.')); showConflicts(error.detail.conflicts); }
    else byId('settings-message').textContent = error.message;
  }
};
