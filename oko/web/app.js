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
let imageUrls = [];
function clearImages() { byId('images').querySelectorAll('video,audio').forEach(media => { media.pause(); media.removeAttribute('src'); media.load(); }); imageUrls.forEach(url => URL.revokeObjectURL(url)); imageUrls = []; byId('images').replaceChildren(); }
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
  clearImages();
  generation++;
  byId('theses').replaceChildren();
  byId('theses-panel').hidden = true;
  selected = null;
  byId('material').hidden = true;
  byId('empty').hidden = false;
  ['title', 'text', 'date', 'message', 'ai-status'].forEach(id => { byId(id).textContent = ''; });
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
  byId('collect').disabled = true;
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
    byId('theses-panel').hidden = !material.ai && !material.theses;
    byId('ai-status').textContent = material.theses ? 'Тезисы готовы' : material.ai?.error || (material.ai?.status === 'running' ? 'Подготовка тезисов' : 'Ожидание обработчика');
    byId('theses-retry').hidden = !material.ai?.error;
    byId('theses').replaceChildren(...(material.theses?.points || []).map(point => {
      const li = element('li', point.text);
      for (const ref of point.sources) { const link = element('a', ' Источник'); link.href = ref.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; li.append(link); }
      return li;
    }));
    byId('original-panel').open = !material.theses;
    byId('completeness').textContent = !material.complete ? 'Загрузка материала' : material.loading ? 'Текст сохранён · загрузка вложений' : material.full === false ? 'Неполный материал · ' + material.reason : 'Полностью сохранено';
    byId('retry').hidden = !material.complete || material.full !== false || material.loading;
    clearImages();
    byId('date').textContent = new Date(material.published * 1000).toLocaleString('ru');
    byId('message').textContent = '';
    byId('material').hidden = false;
    byId('empty').hidden = true;
    for (const path of material.images || []) {
      const response = await fetch(path, {cache: 'no-store', headers: {'Authorization': 'Bearer ' + token}});
      if (!response.ok) continue;
      const blob = await response.blob();
      if (stamp !== generation || stopped || !ids.has(id)) return;
      const url = URL.createObjectURL(blob); imageUrls.push(url);
      const img = document.createElement('img'); img.src = url; img.alt = 'Изображение материала'; byId('images').append(img);
    }
    for (const attachment of material.attachments || []) {
      const response = await fetch(attachment.url, {cache: 'no-store', headers: {'Authorization': 'Bearer ' + token}});
      if (!response.ok) continue;
      const blob = await response.blob();
      if (stamp !== generation || stopped || !ids.has(id)) return;
      const url = URL.createObjectURL(blob); imageUrls.push(url);
      let node;
      if (attachment.kind === 'image') { node = element('img'); node.alt = 'Изображение публикации'; }
      else if (attachment.kind === 'video' || attachment.kind === 'circle' || attachment.kind === 'audio') {
        node = element(attachment.kind === 'audio' ? 'audio' : 'video'); node.controls = true; node.preload = 'metadata';
        node.setAttribute('aria-label', attachment.kind === 'circle' ? 'Кружочек Telegram' : attachment.kind === 'audio' ? 'Аудио публикации' : 'Видео публикации');
        if (attachment.kind === 'circle') node.classList.add('circle');
      } else { node = element('a', 'Скачать вложение'); node.href = url; node.download = attachment.url.split('/').pop(); }
      if (node.tagName !== 'A') node.src = url;
      byId('images').append(node);
    }
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
    byId('source-status').replaceChildren(...(state.sources || []).filter(source => source.error || source.gap).map(source => { const p = document.createElement('p'); p.textContent = source.title + ': ' + (source.error || source.gap); return p; }));
    if (state.error) { clear(); return; }
    const params = new URLSearchParams({sort: byId('sort').value});
    if (folderId) params.set('folder', folderId);
    const cards = await api('cards?' + params);
    if (stopped || epoch !== contentEpoch) return;
    ids = new Set(cards.map(card => card.id));
    if (selected && !ids.has(selected.id)) clear();
    if (selected && cards.some(card => card.id === selected.id && (JSON.stringify(card.theses) !== JSON.stringify(selected.theses) || JSON.stringify(card.ai) !== JSON.stringify(selected.ai) || card.version !== selected.version || card.complete !== selected.complete || card.loading !== selected.loading || card.full !== selected.full || card.reason !== selected.reason || JSON.stringify(card.members) !== JSON.stringify(selected.members) || JSON.stringify(card.attachments) !== JSON.stringify(selected.attachments)))) await open(selected.id);
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
  finally { if (!stopped) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 500); } }
}
byId('collect').onclick = async () => { try { await api('collect', 'POST'); refresh(); } catch (error) { byId('progress').textContent = error.message; } };
byId('retry').onclick = async () => { if (!selected) return; try { await api('materials/' + selected.id + '/retry', 'POST'); byId('retry').hidden = true; refresh(); } catch (error) { byId('message').textContent = error.message; } };
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
  digest_days: ['Период обзора, дней', 'number'], processor: ['Обработчик', 'processor']
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
      else if (type === 'processor') input = selectControl([['codex', 'Codex · существующий вход CLI'], ['ollama', 'Ollama · подключённая модель'], ...(!['codex', 'ollama'].includes(effective.value) ? [[effective.value, effective.value]] : [])], effective.value);
      else if (type === 'text') { input = element('textarea'); input.value = effective.value; input.rows = 3; }
      else { input = element('input'); input.type = type === 'number' ? 'number' : 'text'; input.value = effective.value; if (type === 'number') { input.min = '1'; input.max = '3650'; } }
      input.id = 'setting-' + field;
      const info = element('small', 'Эффективное значение: ' + (type === 'boolean' ? (effective.value ? 'Включено' : 'Выключено') : String(effective.value)) + ' · ' + describeOwner(effective.scope, effective.owner));
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
  const connection = feed.kind === 'demo' ? 'Тестовый источник подключён' : feed.transport === 'telegram' ? 'Telegram: ' + (feed.telegram_peer ? 'подключение настроено' : 'требуется подключение') : feed.transport === 'rss' ? 'RSS подключён: сбор при открытой вкладке' : feed.connection === 'ambiguous' ? 'Адрес неоднозначен: требуется явный выбор подключения' : 'Требуется подключение сборщика';
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
    container.append(element('p', 'Отметки прочитанного в Telegram пока не отправляются. Режим сохраняется для следующего этапа.'), labelled('Автопрочитывание в Telegram', auto), labelled('Отметить историю до начала импорта прочитанной', history), element('p', 'Без разрешения очистки старая непрочитанная история может задержать отметки Telegram. Сохранение настроек не отправляет отметки.'));
  }
  if (feed.kind === 'telegram' || feed.kind === 'unresolved') {
    transport = selectControl([['', 'Подключение не выбрано'], ...(feed.kind === 'telegram' ? [['telegram', 'Telegram с локальными медиа']] : []), ['rss', 'Ограниченный RSS (без Telegram-медиа и отметок чтения)']], feed.transport || '');
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
  if (feed.kind === 'telegram') {
    const peer = element('input'); peer.value = feed.telegram_peer || feed.identity.replace(/^telegram:/, '@');
    const account = element('input'); account.value = feed.telegram_account || '';
    const connect = element('button', 'Подключить Telegram-фид');
    connect.onclick = async () => {
      try {
        await api('telegram/connect', 'POST', {account: account.value, peer: peer.value, folder: subscriptions.folders.find(f => f.id === feed.folder_id).title, feed_id: feed.id, auto_read: auto.value === 'true'});
        await loadSubscriptions('feed/' + feed.id); byId('settings-message').textContent = 'Подключён; отметки прочитанного пока не отправляются';
      } catch (error) { byId('settings-message').textContent = error.message; }
    };
    container.append(labelled('Аккаунт существующего клиента', account), labelled('Username или ID Telegram', peer), connect);
  }
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
  try { await loadSubscriptions(); const config = await api('processors/ollama'); if (config) { byId('ollama-endpoint').value = config.endpoint; byId('ollama-model').value = config.model; } } catch (error) { byId('settings-message').textContent = error.message; }
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

byId('telegram-accounts').onclick = async () => {
  try { byId('settings-message').textContent = 'Доступные аккаунты: ' + (await api('telegram/accounts')).join(', '); }
  catch (error) { byId('settings-message').textContent = error.message; }
};
byId('telegram-add').onclick = async () => {
  try {
    const feed = await api('telegram/connect', 'POST', {account: byId('telegram-account').value, peer: byId('telegram-peer').value, folder: byId('telegram-folder').value, auto_read: byId('telegram-auto').checked});
    await loadSubscriptions('feed/' + feed.id); contentEpoch++; refresh();
    byId('settings-message').textContent = 'Источник подключён; отметки прочитанного пока не отправляются';
  } catch (error) { byId('settings-message').textContent = error.message; }
};

byId('ollama-save').onclick = async () => {
  try { await api('processors/ollama', 'PUT', {endpoint: byId('ollama-endpoint').value, model: byId('ollama-model').value}); byId('ollama-status').textContent = 'Подключение сохранено. Выберите Ollama и включите тезисы для Око, папки или фида.'; }
  catch (error) { byId('ollama-status').textContent = error.message; }
};
byId('ollama-check').onclick = async () => {
  try { byId('ollama-status').textContent = 'Проверка…'; const result = await api('processors/ollama/check', 'POST'); if (stopped) return; byId('ollama-status').textContent = result.mode + ' · ' + result.model; byId('ollama-models').replaceChildren(...result.models.map(name => { const option = element('option'); option.value = name; return option; })); }
  catch (error) { byId('ollama-status').textContent = error.message; }
};
byId('theses-retry').onclick = async () => {
  if (!selected) return;
  try { await api('materials/' + selected.id + '/theses/retry', 'POST'); refresh(); }
  catch (error) { byId('message').textContent = error.message; }
};

byId('codex-check').onclick = async () => {
  const button = byId('codex-check');
  button.disabled = true;
  byId('codex-status').textContent = 'Проверка CLI и входа…';
  try {
    const result = await api('processors/codex/check', 'POST');
    if (stopped) return;
    const auth = {chatgpt: 'вход через ChatGPT', api_key: 'вход через API key', missing: 'вход отсутствует', unknown: 'способ входа не распознан', unchecked: 'вход не проверен'};
    byId('codex-status').textContent = (result.version ? 'CLI ' + result.version + ' · ' : '') + auth[result.auth] + '. Лимиты проверяются при выполнении задания. ' + (result.available ? 'Изолированный запуск доступен.' : result.reason);
  } catch (error) {
    if (!stopped) byId('codex-status').textContent = error.message;
  } finally {
    button.disabled = stopped;
  }
};
