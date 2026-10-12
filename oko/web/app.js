import {historyProgress} from './initial-history.js';
import {createFeedConnection} from './feed-connection.js';
import {createDebugPanel, reportError} from './debug.js';
import {createTelegramConnection} from './telegram-connection.js';
import {createOpenedMaterial} from './opened-material.js';

'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const debug = createDebugPanel({token});
const tab = crypto.randomUUID();
const byId = id => document.getElementById(id);
const bus = new BroadcastChannel('oko-reader');
let contentEpoch = 0;
let stopped = false;
let folderId = null;
let refreshTimer = null;
async function api(path, method = 'GET', body = undefined, signal = undefined) {
  try {
    const response = await fetch('/api/' + path, {method, signal, cache: 'no-store', headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(typeof result.detail === 'string' ? result.detail : response.status === 422 ? 'Проверьте значения настроек' : 'Связь с Око потеряна');
      error.detail = result.detail;
    error.diagnosticId = response.headers.get('X-Oko-Error');
    if (response.headers.get('X-Oko-Expected')) error.name = 'AbortError';
      throw error;
    }
    return result;
  } catch (error) { reportError(error, method + ' /api/' + path, true); throw error; }
}

const opened = createOpenedMaterial({
  api,
  fetchMedia: (path, signal) => fetch(path, {signal, cache: 'no-store', headers: {'Authorization': 'Bearer ' + token}}),
  onViewed: id => { contentEpoch++; opened.viewed(id); bus.postMessage(id); refresh(); },
  onChanged: changed => { if (changed) contentEpoch++; refresh(); },
});
const telegram = createTelegramConnection({api, onConnected: async feed => {
  await loadSubscriptions('feed/' + feed.id); contentEpoch++; refresh();
}});
const feedConnection = createFeedConnection({api, onSelected: async feed => {
  await loadSubscriptions('feed/' + feed.id); contentEpoch++; refresh();
}});
function stopUi(message) {
  feedConnection.stop();
  telegram.stop();
  stopped = true;
  contentEpoch++;
  opened.clear();
  byId('cards').replaceChildren();
  byId('folders').replaceChildren();
  byId('count').textContent = '0';
  byId('progress').textContent = message;
  byId('stop').disabled = true;
  byId('collect').disabled = true;
  byId('compress').disabled = true;
  byId('folder-viewed').disabled = true;
  byId('completion-dialog').close();
  byId('digest-now').disabled = true;
  byId('digest-dialog').close();
  byId('settings-dialog').close();
  byId('settings-open').disabled = true;
  byId('rss-add').disabled = true;
  void debug.refresh();
}
bus.onmessage = event => {
  if (event.data === 'stop') stopUi('Око остановлено');
  else if (event.data?.kind === 'folder-viewed') {
    contentEpoch++;
    opened.folderCompleted(event.data.folder);
    refresh();
  } else {
    contentEpoch++;
    opened.viewed(event.data);
  }
};
async function refresh() {
  clearTimeout(refreshTimer);
  if (stopped) return;
  const epoch = contentEpoch;
  try {
    const state = await api('status');
    if (stopped || epoch !== contentEpoch) return;
    debug.updateStatus(state.debug_count, state.debug_persistence_error);
    const historyNode = byId('history-progress');
    if (historyNode) {
      const progress = (state.initial_histories || []).find(h => h.feed_id === historyNode.dataset.feed);
      if (progress) historyNode.textContent = historyProgress(progress);
    }
    if (state.stopped) { stopUi('Око остановлено'); return; }
    const readMessages = (state.telegram_reads || []).filter(r => r.pending || r.waiting || r.error).map(r => r.error || r.waiting || `Telegram: ожидают подтверждения ${r.pending}`);
    const progressMessages = [...new Set([...state.errors, ...readMessages])];
    byId('progress').textContent = state.error || `${state.stage} · источники ${state.ready_sources}/${state.total_sources}` + (progressMessages.length ? ' · ' + progressMessages.join(', ') : '');
    const changesOpen = !!byId('source-status').querySelector('details[open]');
    byId('source-status').replaceChildren(...(state.sources || []).filter(source => source.error || source.gap).map(source => element('p', source.title + ': ' + [source.error, source.gap].filter(Boolean).join(' · '))));
    const changes = (state.sources || []).filter(source => source.changes);
    if (changes.length) {
      const details = element('details'); details.open = changesOpen; details.append(element('summary', 'Возможности проверки изменений'));
      for (const source of changes) details.append(element('p', source.title + ': ' + source.changes));
      byId('source-status').append(details);
    }
    if (state.error) { opened.clear(); return; }
    const params = new URLSearchParams({sort: byId('sort').value});
    if (byId('show-hidden').checked) params.set('show_hidden', 'true');
    if (folderId) params.set('folder', folderId);
    const cards = await api('cards?' + params);
    if (stopped || epoch !== contentEpoch) return;
    opened.reconcile(cards);
    byId('compress').disabled = !folderId;
    byId('folder-viewed').disabled = !folderId;
    byId('digest-now').disabled = !folderId;
    byId('count').textContent = cards.filter(card => !card.filter?.hidden).length;
    byId('cards').replaceChildren(...cards.map(card => {
      const button = document.createElement('button');
      button.classList.toggle('active', opened.selection?.id === card.id);
      button.textContent = (card.digest ? 'Обзор · ' : card.event ? 'Событие · ' : card.filter?.hidden ? 'Скрыто · ' : '') + card.title + (card.changed ? ' · Изменено' : '');
      const date = document.createElement('small');
      date.textContent = new Date(card.published * 1000).toLocaleString('ru');
      button.append(date);
      button.onclick = () => opened.open(card);
      return button;
    }));
    const folders = await api('folders');
    if (stopped || epoch !== contentEpoch) return;
    byId('folders').replaceChildren(...folders.map(folder => {
      const button = document.createElement('button');
      button.textContent = `${folder.title} · ${folder.count}`;
      button.classList.toggle('active', folder.id === folderId);
      button.onclick = () => { folderId = folder.id; contentEpoch++; opened.clear(); refresh(); };
      return button;
    }));
  } catch (error) { reportError(error, 'Интерфейс Око'); if (!stopped && epoch === contentEpoch) { opened.clear(); byId('progress').textContent = error.message; } }
  finally { if (!stopped) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 500); } }
}
byId('collect').onclick = async () => { try { await api('collect', 'POST'); refresh(); } catch (error) { reportError(error, 'Интерфейс Око'); byId('progress').textContent = error.message; } };
byId('stop').onclick = async () => {
  try { await api('stop', 'POST'); bus.postMessage('stop'); stopUi('Око остановлено'); }
  catch (error) { reportError(error, 'Интерфейс Око'); byId('progress').textContent = error.message; }
};
async function heartbeat() {
  if (stopped) return;
  try { await api('tabs/' + tab + '/heartbeat', 'POST'); }
  catch (error) { reportError(error, 'Интерфейс Око'); if (!stopped) { opened.clear(); byId('progress').textContent = error.message; } }
  if (!stopped) setTimeout(heartbeat, 2000);
}
window.addEventListener('pagehide', () => {
  fetch('/api/tabs/' + tab + '/close', {method:'POST', keepalive:true, headers:{'Authorization':'Bearer '+token}}).catch(error => reportError(error, 'Закрытие вкладки'));
  opened.clear();
});
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
byId('all-folders').onclick = () => { folderId = null; contentEpoch++; opened.clear(); refresh(); };
byId('sort').onchange = () => { contentEpoch++; refresh(); };
heartbeat().then(refresh);

const fields = {
  events: ['Объединять события (Око или папка)', 'boolean'], event_hours: ['Окно события, часов (Око или папка)', 'number'],
  originals: ['Оригиналы при открытии публикации', 'boolean'], theses: ['Тезисы публикаций', 'boolean'],
  digests: ['Обзоры за период', 'boolean'], prompt_theses: ['Промпт тезисов', 'text'],
  prompt_digest: ['Промпт обзора', 'text'], prompt_filters: ['Правила фильтрации (промпт; пустое поле выключает фильтрацию)', 'text'],
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
  byId('rss-folders').replaceChildren(...subscriptions.folders.map(folder => { const option = element('option'); option.value = folder.title; return option; }));
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
    if (path.startsWith('feed/') || path.startsWith('folder/')) {
      const update = element('button', 'Обновить обзор сейчас');
      update.onclick = async () => {
        try { await api('digests/' + path, 'POST', {period: true}); byId('settings-message').textContent = 'Обзор поставлен в обработку'; refresh(); }
        catch (error) { reportError(error, 'Интерфейс Око'); byId('settings-message').textContent = error.message; }
      };
      container.append(update);
    }
    for (const [field, [title, type]] of Object.entries(fields)) {
      if (path.startsWith('feed/') && ['events', 'event_hours'].includes(field)) continue;
      const effective = result.effective[field];
      const row = element('div'); row.className = 'setting-row';
      let input;
      if (type === 'boolean') input = selectControl([['true', 'Включено'], ['false', 'Выключено']], effective.value);
      else if (type === 'processor') input = selectControl([['codex', 'Codex · существующий вход CLI'], ['llamacpp', 'llama.cpp · подключённая модель'], ...(!['codex', 'llamacpp'].includes(effective.value) ? [[effective.value, effective.value]] : [])], effective.value);
      else if (type === 'text') { input = element('textarea'); input.value = effective.value; input.rows = 3; }
      else { input = element('input'); input.type = type === 'number' ? 'number' : 'text'; input.value = effective.value; if (type === 'number') { input.min = '1'; input.max = field === 'event_hours' ? '87600' : '3650'; } }
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
        } catch (error) { reportError(error, 'Интерфейс Око'); byId('settings-message').textContent = error.message; }
      }
      save.onclick = () => update(type === 'boolean' ? input.value === 'true' : type === 'number' ? Number(input.value) : input.value);
      reset.onclick = () => update(null);
      row.append(labelled(title, input), info, save, reset); container.append(row);
    }
  } catch (error) { reportError(error, 'Интерфейс Око'); byId('settings-message').textContent = error.message; }
}
function renderFeed(feed) {
  const container = byId('feed-options');
  container.append(element('h2', 'Подключение фида'));
  if (feed.initial_history) {
    const progress = element('p', historyProgress(feed.initial_history)); progress.id = 'history-progress'; progress.dataset.feed = feed.id; progress.setAttribute('role', 'status'); container.append(progress);
  }
  const connection = feed.kind === 'demo' ? 'Тестовый источник подключён' : feed.transport === 'telegram' ? 'Telegram: ' + (feed.telegram_peer ? 'подключение настроено' : 'требуется подключение') : feed.transport === 'rss' ? 'RSS подключён: сбор при открытой вкладке' : feed.transport === 'youtube' ? 'YouTube подключён: сбор при открытой вкладке' : feed.kind === 'youtube' ? 'YouTube отключён' : feed.connection === 'ambiguous' ? 'Адрес неоднозначен: требуется явный выбор подключения' : 'Требуется подключение сборщика';
  container.append(element('p', connection), element('p', 'Исходные адреса: ' + feed.original_urls.join(', ')));
  if (feed.kind === 'youtube') container.append(element('p', (feed.transport === 'youtube' ? 'YouTube: обновления собираются при открытой вкладке. ' : 'YouTube подключается автоматически при добавлении URL фида. ') + 'Высокая важность автоматически включает субтитры и тезисы; для остальных включите режим «Тезисы». Видео не скачивается.'));
  const folder = selectControl(subscriptions.folders.map(f => [f.id, f.title]), feed.folder_id);
  const importance = selectControl(Object.entries(importanceNames), feed.importance);
  const days = element('input'); days.type = 'number'; days.min = '1'; days.max = '3650'; days.value = feed.initial_days;
  const publicAi = element('input'); publicAi.type = 'checkbox'; publicAi.checked = feed.public_ai;
  container.append(labelled('Папка', folder), labelled('Важность фида', importance), labelled('Первоначальный период, дней', days), labelled('Разрешить обработку публичным AI', publicAi));
  let auto, history, transport, youtubeEnabled;
  if (feed.kind === 'telegram') {
    auto = selectControl([['', 'Выберите режим'], ['true', 'Автопрочитывать после полного сохранения'], ['false', 'Отмечать только после «Просмотрено»']], feed.auto_read === null ? '' : feed.auto_read);
    history = element('input'); history.type = 'checkbox'; history.checked = feed.read_history;
    container.append(element('p', 'Око подтверждает только безопасную границу истории. Просмотр позднего материала может ожидать более ранний.'), labelled('Автопрочитывание в Telegram', auto), labelled('Отметить историю до начала импорта прочитанной', history), element('p', 'Без разрешения очистки старая непрочитанная история может задержать отметки Telegram. Разрешение применяется при работающем Око только к истории до зафиксированного начала импорта.'));
  }
  if (feed.kind === 'youtube') {
    youtubeEnabled = element('input'); youtubeEnabled.type = 'checkbox'; youtubeEnabled.checked = feed.transport === 'youtube';
    container.append(labelled('Собирать обновления YouTube', youtubeEnabled));
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
    if (youtubeEnabled) patch.transport = youtubeEnabled.checked ? 'youtube' : null;
    try {
      await api('feeds/' + feed.id, 'PATCH', patch);
      await loadSubscriptions('feed/' + feed.id);
      contentEpoch++; refresh();
      byId('settings-message').textContent = 'Фид сохранён';
    } catch (error) { reportError(error, 'Интерфейс Око'); byId('settings-message').textContent = error.message; }
  };
  container.append(save);
  if (feed.kind === 'telegram') {
    const connect = element('button', 'Настроить подключение Telegram-фида');
    connect.onclick = () => telegram.configure(feed, subscriptions.folders.find(f => f.id === feed.folder_id).title);
    container.append(connect);
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
  telegram.loadAccounts();
  byId('settings-message').textContent = '';
  try { await loadSubscriptions(); const config = await api('processors/llamacpp'); if (config) { byId('llamacpp-endpoint').value = config.endpoint; byId('llamacpp-model').value = config.model; byId('llamacpp-local').checked = config.local_execution; } } catch (error) { reportError(error, 'Интерфейс Око'); byId('settings-message').textContent = error.message; }
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
  } catch (error) { reportError(error, 'Интерфейс Око'); importText = null; byId('settings-message').textContent = error.message; }
};
byId('import-confirm').onclick = async () => {
  if (!importText) return;
  try {
    await api('import', 'POST', {opml: importText, choices: importChoices});
    importText = null; importChoices = {}; byId('opml-file').value = ''; byId('import-confirm').hidden = true;
    byId('import-plan').replaceChildren(element('p', 'Подписки импортированы. RSS и YouTube подключены автоматически; для Telegram настройте подключение.'));
    await loadSubscriptions(); contentEpoch++; refresh();
    byId('settings-message').textContent = 'Импорт завершён';
  } catch (error) { reportError(error, 'Интерфейс Око');
    if (error.detail?.conflicts) { byId('import-plan').replaceChildren(element('p', 'Выберите одну папку для каждого конфликта и повторите импорт.')); showConflicts(error.detail.conflicts); }
    else byId('settings-message').textContent = error.message;
  }
};

for (const id of ['llamacpp-endpoint', 'llamacpp-model']) {
  byId(id).oninput = () => { byId('llamacpp-local').checked = false; };
}
byId('llamacpp-save').onclick = async () => {
  try { await api('processors/llamacpp', 'PUT', {endpoint: byId('llamacpp-endpoint').value, model: byId('llamacpp-model').value, local_execution: byId('llamacpp-local').checked}); byId('llamacpp-status').textContent = 'Подключение сохранено. Выберите llama.cpp и включите тезисы для Око, папки или фида.'; }
  catch (error) { reportError(error, 'Интерфейс Око'); byId('llamacpp-status').textContent = error.message; }
};
byId('llamacpp-check').onclick = async () => {
  try { byId('llamacpp-status').textContent = 'Проверка…'; const result = await api('processors/llamacpp/check', 'POST'); if (stopped) return; byId('llamacpp-status').textContent = result.mode + ' · ' + result.model; byId('llamacpp-models').replaceChildren(...result.models.map(name => { const option = element('option'); option.value = name; return option; })); }
  catch (error) { reportError(error, 'Интерфейс Око'); byId('llamacpp-status').textContent = error.message; }
};
byId('codex-check').onclick = async () => {
  const button = byId('codex-check');
  button.disabled = true;
  byId('codex-status').textContent = 'Проверка CLI и входа…';
  try {
    const result = await api('processors/codex/check', 'POST');
    if (stopped) return;
    const auth = {chatgpt: 'вход через ChatGPT', api_key: 'вход через API key', missing: 'вход отсутствует', unknown: 'способ входа не распознан', unchecked: 'вход не проверен'};
    byId('codex-status').textContent = (result.version ? 'CLI ' + result.version + ' · ' : '') + auth[result.auth] + (result.limits_checked ? '. Лимиты проверены. ' : '. Лимиты не проверены. ') + (result.available ? 'Изолированный запуск доступен.' : result.reason);
  } catch (error) { reportError(error, 'Интерфейс Око');
    if (!stopped) byId('codex-status').textContent = error.message;
  } finally {
    button.disabled = stopped;
  }
};

byId('show-hidden').onchange = () => { contentEpoch++; opened.clear(); refresh(); };
let digestPeriod = false;
function openDigest(period) {
  if (!folderId || stopped) return;
  digestPeriod = period;
  byId('digest-heading').textContent = period ? 'Обновить обзор за период' : 'Сжать историю папки';
  byId('digest-start').value = ''; byId('digest-end').value = '';
  byId('digest-message').textContent = period ? 'Без дат используется настроенный период обзора.' : '';
  byId('digest-dialog').showModal();
}
byId('compress').onclick = () => openDigest(false);
byId('digest-now').onclick = () => openDigest(true);
byId('digest-close').onclick = () => byId('digest-dialog').close();
byId('digest-run').onclick = async () => {
  const button = byId('digest-run'); button.disabled = true;
  try {
    const body = {period: digestPeriod};
    for (const field of ['start', 'end']) {
      const value = byId('digest-' + field).value;
      if (value) body[field] = new Date(value).getTime() / 1000;
    }
    await api('digests/folder/' + folderId, 'POST', body);
    byId('digest-dialog').close(); refresh();
  } catch (error) { reportError(error, 'Интерфейс Око'); byId('digest-message').textContent = error.message; }
  finally { button.disabled = stopped; }
};

let completionFolder = null;
let completionToken = null;
let completionGeneration = 0;
function renderCompletion(preview) {
  completionToken = preview.token;
  byId('completion-count').textContent = `Карточек: ${preview.cards}. Исходных публикаций: ${preview.originals}.`;
  byId('completion-confirm').disabled = stopped || preview.originals === 0;
}
byId('folder-viewed').onclick = async () => {
  if (!folderId || stopped) return;
  const stamp = ++completionGeneration;
  completionFolder = folderId;
  completionToken = null;
  byId('completion-count').textContent = 'Подсчёт…';
  byId('completion-message').textContent = '';
  byId('completion-confirm').disabled = true;
  byId('completion-dialog').showModal();
  try {
    const preview = await api('folders/' + completionFolder + '/completion/preview', 'POST');
    if (stamp === completionGeneration && !stopped && byId('completion-dialog').open) renderCompletion(preview);
  } catch (error) { reportError(error, 'Интерфейс Око'); if (stamp === completionGeneration) byId('completion-message').textContent = error.message; }
};
byId('completion-dialog').addEventListener('close', () => { completionGeneration++; completionToken = null; });
byId('completion-close').onclick = () => byId('completion-dialog').close();
byId('completion-confirm').onclick = async () => {
  if (!completionToken || stopped) return;
  const stamp = completionGeneration;
  const confirmedFolder = completionFolder;
  byId('completion-confirm').disabled = true;
  try {
    await api('folders/' + confirmedFolder + '/completion', 'POST', {token: completionToken});
    contentEpoch++;
    opened.folderCompleted(confirmedFolder);
    byId('cards').replaceChildren();
    bus.postMessage({kind: 'folder-viewed', folder: confirmedFolder});
    byId('completion-dialog').close();
    refresh();
  } catch (error) { reportError(error, 'Интерфейс Око');
    if (!error.detail?.preview) {
      contentEpoch++;
      opened.folderCompleted(confirmedFolder);
      bus.postMessage({kind: 'folder-viewed', folder: confirmedFolder});
    }
    refresh();
    if (stamp !== completionGeneration || stopped) return;
    byId('completion-message').textContent = error.detail?.message || error.message;
    if (error.detail?.preview) renderCompletion(error.detail.preview);
    else byId('completion-confirm').disabled = false;
  }
};
