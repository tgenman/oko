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
let cardFolders = new Map();
let folderId = null;
let refreshTimer = null;
let imageUrls = [];
function clearImages() { byId('material').querySelectorAll('video,audio').forEach(media => { media.pause(); media.removeAttribute('src'); media.load(); }); imageUrls.forEach(url => URL.revokeObjectURL(url)); imageUrls = []; byId('images').replaceChildren(); }
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
  byId('event-panel').hidden = true;
  byId('event-body').replaceChildren();
  byId('event-sources').replaceChildren();
  byId('filter-panel').hidden = true;
  ['filtered-text', 'filter-status'].forEach(id => { byId(id).textContent = ''; });
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
  byId('compress').disabled = true;
  byId('folder-viewed').disabled = true;
  byId('completion-dialog').close();
  byId('digest-now').disabled = true;
  byId('digest-dialog').close();
  byId('settings-dialog').close();
  byId('settings-open').disabled = true;
}
bus.onmessage = event => {
  if (event.data === 'stop') stopUi('Око остановлено');
  else if (event.data?.kind === 'folder-viewed') {
    contentEpoch++;
    if (selected?.folder_id === event.data.folder) clear();
    for (const [id, folder] of cardFolders) { if (folder === event.data.folder) ids.delete(id); }
    refresh();
  } else {
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
    clearImages();
    byId('event-panel').hidden = !(material.event || material.digest);
    byId('event-body').replaceChildren();
    byId('event-sources').replaceChildren();
    byId('original-panel').hidden = !!(material.event || material.digest);
    byId('copy').hidden = !!(material.event || material.digest);
    if (material.event || material.digest) renderEvent(material);
    selected = {...material, folder_id: material.folder_id || cardFolders.get(id)};
    byId('title').textContent = material.title;
    byId('text').textContent = material.text;
    const filter = material.filter;
    byId('filter-panel').hidden = !filter || filter.status === 'off';
    byId('filter-status').textContent = !filter || filter.status === 'off' ? '' : filter.status === 'done' ? (filter.restored ? 'Возвращено пользователем · ' : filter.hidden ? 'Скрыто · ' : filter.removed.length ? 'Рекламные вставки убраны · ' : 'Фильтрация завершена') + (filter.reason || '') : filter.error || 'Ожидание фильтрации';
    byId('filter-restore').hidden = !filter || filter.status !== 'done' || filter.restored || !(filter.hidden || filter.removed.length);
    byId('filter-retry').hidden = !filter?.error;
    byId('filtered-text').textContent = filter?.status === 'done' && !filter.hidden && !filter.restored && filter.removed.length ? material.display_text : '';

    byId('theses-panel').hidden = !material.ai && !material.theses;
    byId('ai-status').textContent = material.theses ? 'Тезисы готовы' : material.ai?.error || (material.ai?.status === 'running' ? 'Подготовка тезисов' : 'Ожидание обработчика');
    byId('theses-retry').hidden = !material.ai?.error;
    byId('theses').replaceChildren(...(material.theses?.points || []).map(point => {
      const li = element('li', point.text);
      for (const ref of point.sources) { const link = element('a', ' Источник'); link.href = ref.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; li.append(link); }
      return li;
    }));
    byId('original-panel').open = material.originals !== false && (!filter || filter.status !== 'done' || filter.restored || !filter.removed.length && !filter.hidden);
    byId('completeness').textContent = !material.complete ? 'Загрузка материала' : material.loading ? (material.captions ? 'Получение субтитров и превью' : 'Текст сохранён · загрузка вложений') : material.captions ? (material.reason || 'Субтитры и превью сохранены') : material.full === false ? 'Неполный материал · ' + material.reason : 'Полностью сохранено';
    byId('retry').hidden = !material.complete || material.full !== false || material.loading;
    byId('date').textContent = new Date(material.published * 1000).toLocaleString('ru');
    byId('message').textContent = '';
    byId('material').hidden = false;
    byId('empty').hidden = true;
    await appendMedia(material, byId('images'), stamp, id);
    if (material.event || material.digest) {
      for (const source of material.sources) {
        if (stamp !== generation || stopped) return;
        await appendMedia(source, byId('source-media-' + source.id), stamp, id);
      }
    }
  } catch (error) { clear(); byId('progress').textContent = error.message; }
}
async function appendMedia(material, container, stamp, id) {
    for (const path of material.images || []) {
      const response = await fetch(path, {cache: 'no-store', headers: {'Authorization': 'Bearer ' + token}});
      if (!response.ok) continue;
      const blob = await response.blob();
      if (stamp !== generation || stopped || !ids.has(id)) return;
      const url = URL.createObjectURL(blob); imageUrls.push(url);
      const img = document.createElement('img'); img.src = url; img.alt = 'Изображение материала'; container.append(img);
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
      container.append(node);
    }
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
    if (byId('show-hidden').checked) params.set('show_hidden', 'true');
    if (folderId) params.set('folder', folderId);
    const cards = await api('cards?' + params);
    if (stopped || epoch !== contentEpoch) return;
    ids = new Set(cards.map(card => card.id));
    cardFolders = new Map(cards.map(card => [card.id, card.folder_id]));
    if (selected && !ids.has(selected.id)) clear();
    if (selected && cards.some(card => card.id === selected.id && (JSON.stringify(card.digest) !== JSON.stringify(selected.digest) || JSON.stringify(card.event) !== JSON.stringify(selected.event) || JSON.stringify(card.filter) !== JSON.stringify(selected.filter) || JSON.stringify(card.theses) !== JSON.stringify(selected.theses) || JSON.stringify(card.ai) !== JSON.stringify(selected.ai) || card.originals !== selected.originals || card.version !== selected.version || card.complete !== selected.complete || card.loading !== selected.loading || card.full !== selected.full || card.reason !== selected.reason || JSON.stringify(card.members) !== JSON.stringify(selected.members) || JSON.stringify(card.attachments) !== JSON.stringify(selected.attachments)))) await open(selected.id);
    byId('compress').disabled = !folderId;
    byId('folder-viewed').disabled = !folderId;
    byId('digest-now').disabled = !folderId;
    byId('count').textContent = cards.filter(card => !card.filter?.hidden).length;
    byId('cards').replaceChildren(...cards.map(card => {
      const button = document.createElement('button');
      button.classList.toggle('active', selected?.id === card.id);
      button.textContent = (card.digest ? 'Обзор · ' : card.event ? 'Событие · ' : card.filter?.hidden ? 'Скрыто · ' : '') + card.title;
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
        catch (error) { byId('settings-message').textContent = error.message; }
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
  const connection = feed.kind === 'demo' ? 'Тестовый источник подключён' : feed.transport === 'telegram' ? 'Telegram: ' + (feed.telegram_peer ? 'подключение настроено' : 'требуется подключение') : feed.transport === 'rss' ? 'RSS подключён: сбор при открытой вкладке' : feed.transport === 'youtube' ? 'YouTube подключён: сбор при открытой вкладке' : feed.kind === 'youtube' ? 'YouTube отключён: выберите способ сбора' : feed.connection === 'ambiguous' ? 'Адрес неоднозначен: требуется явный выбор подключения' : 'Требуется подключение сборщика';
  container.append(element('p', connection), element('p', 'Исходные адреса: ' + feed.original_urls.join(', ')));
  if (feed.kind === 'youtube') container.append(element('p', (feed.transport === 'youtube' ? 'YouTube: обновления собираются при открытой вкладке. ' : 'Для сбора выберите способ подключения YouTube. ') + 'Высокая важность автоматически включает субтитры и тезисы; для остальных включите режим «Тезисы». Видео не скачивается.'));
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
  if (feed.kind === 'telegram' || feed.kind === 'unresolved' || feed.kind === 'youtube') {
    transport = selectControl([['', 'Подключение не выбрано'], ...(feed.kind === 'youtube' ? [['youtube', 'YouTube: обновления и субтитры']] : [...(feed.kind === 'telegram' ? [['telegram', 'Telegram с локальными медиа']] : []), ['rss', 'Ограниченный RSS (без Telegram-медиа и отметок чтения)']])], feed.transport || '');
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
  try { await loadSubscriptions(); const config = await api('processors/llamacpp'); if (config) { byId('llamacpp-endpoint').value = config.endpoint; byId('llamacpp-model').value = config.model; byId('llamacpp-local').checked = config.local_execution; } } catch (error) { byId('settings-message').textContent = error.message; }
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

for (const id of ['llamacpp-endpoint', 'llamacpp-model']) {
  byId(id).oninput = () => { byId('llamacpp-local').checked = false; };
}
byId('llamacpp-save').onclick = async () => {
  try { await api('processors/llamacpp', 'PUT', {endpoint: byId('llamacpp-endpoint').value, model: byId('llamacpp-model').value, local_execution: byId('llamacpp-local').checked}); byId('llamacpp-status').textContent = 'Подключение сохранено. Выберите llama.cpp и включите тезисы для Око, папки или фида.'; }
  catch (error) { byId('llamacpp-status').textContent = error.message; }
};
byId('llamacpp-check').onclick = async () => {
  try { byId('llamacpp-status').textContent = 'Проверка…'; const result = await api('processors/llamacpp/check', 'POST'); if (stopped) return; byId('llamacpp-status').textContent = result.mode + ' · ' + result.model; byId('llamacpp-models').replaceChildren(...result.models.map(name => { const option = element('option'); option.value = name; return option; })); }
  catch (error) { byId('llamacpp-status').textContent = error.message; }
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
    byId('codex-status').textContent = (result.version ? 'CLI ' + result.version + ' · ' : '') + auth[result.auth] + (result.limits_checked ? '. Лимиты проверены. ' : '. Лимиты не проверены. ') + (result.available ? 'Изолированный запуск доступен.' : result.reason);
  } catch (error) {
    if (!stopped) byId('codex-status').textContent = error.message;
  } finally {
    button.disabled = stopped;
  }
};

byId('show-hidden').onchange = () => { contentEpoch++; clear(); refresh(); };
byId('filter-restore').onclick = async () => {
  if (!selected) return;
  try { await api('materials/' + selected.id + '/filter/restore', 'POST'); contentEpoch++; refresh(); }
  catch (error) { byId('message').textContent = error.message; }
};
byId('filter-retry').onclick = async () => {
  if (!selected) return;
  try { await api('materials/' + selected.id + '/filter/retry', 'POST'); refresh(); }
  catch (error) { byId('message').textContent = error.message; }
};

function renderEvent(material) {
  const body = byId('event-body');
  for (const [field, title] of [['points', 'Сводка'], ['updates', 'Обновление'], ['conflicts', 'Противоречия источников']]) {
    const points = (material.event || material.digest)[field] || [];
    if (!points.length) continue;
    body.append(element('h2', title));
    const list = element('ul');
    for (const point of points) {
      const li = element('li', point.text);
      for (const ref of point.sources) {
        const source = material.sources.find(s => s.id === ref.id);
        const link = element('a', ' · ' + (source?.title || 'Источник'));
        link.href = ref.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; li.append(link);
      }
      list.append(li);
    }
    body.append(list);
  }
  for (const source of material.sources) {
    const section = element('section');
    const link = element('a', source.title); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    const copy = element('button', 'Скопировать ссылку');
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(source.url); byId('message').textContent = 'Ссылка скопирована'; }
      catch { byId('message').textContent = 'Браузер не разрешил копирование'; }
    };
    const details = element('details');
    const text = element('p', source.text); text.className = 'source-text';
    details.append(element('summary', 'Оригинал · ' + new Date(source.published * 1000).toLocaleString('ru')), text);
    const media = element('div'); media.className = 'source-media'; media.id = 'source-media-' + source.id; details.append(media);
    section.append(link, copy, details); byId('event-sources').append(section);
  }
}

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
  } catch (error) { byId('digest-message').textContent = error.message; }
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
  } catch (error) { if (stamp === completionGeneration) byId('completion-message').textContent = error.message; }
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
    clear();
    byId('cards').replaceChildren();
    bus.postMessage({kind: 'folder-viewed', folder: confirmedFolder});
    byId('completion-dialog').close();
    refresh();
  } catch (error) {
    if (!error.detail?.preview) {
      contentEpoch++;
      if (selected?.folder_id === confirmedFolder) clear();
      bus.postMessage({kind: 'folder-viewed', folder: confirmedFolder});
    }
    refresh();
    if (stamp !== completionGeneration || stopped) return;
    byId('completion-message').textContent = error.detail?.message || error.message;
    if (error.detail?.preview) renderCompletion(error.detail.preview);
    else byId('completion-confirm').disabled = false;
  }
};
