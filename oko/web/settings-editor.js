import {historyProgress} from './initial-history.js';
import {reportError} from './debug.js';
import {createOperations} from './ui-operations.js';
import {element, selectControl, labelled} from './controls.js';
import {createTelegramConnection} from './telegram-connection.js';
import {createYouTubeHistory} from './youtube-history.js';

export function createSettingsEditor({api, onChanged, onDetail, onFolders}) {
  const byId = id => document.getElementById(id);
  const reconnect = createTelegramConnection({api, prefix: 'reconnect', context: () => operations.capture(), onConnected: async (_feed, action) => {onChanged(); await loadSubscriptions(); await action.commit(loadSettings);}});
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
  let sourceStatus = [];
  function sourceHelp() {
    const target = document.getElementById('source-help');
    if (target) {target.textContent = sourceStatus.find(s => currentPath === 'feed/' + s.feed_id)?.changes || ''; target.parentElement.hidden = !target.textContent;}
  }
  let selectedOwner = null, currentPath = null;
  let youtubeHistory = null;
  const drafts = new Map();
  const operations = createOperations();
  const subscriptionOperations = createOperations();
  function draft(path) { if (!drafts.has(path)) drafts.set(path, {}); return drafts.get(path); }
  function scopePath() { return currentPath; }
  function editorMessage(message) { byId('settings-message').textContent = message; }
  function describeOwner(scope, owner) {
    if (scope === 'default') return 'Око · начальное значение';
    if (scope === 'oko') return 'Око';
    const list = scope === 'folder' ? subscriptions.folders : subscriptions.feeds;
    return (scope === 'folder' ? 'Папка: ' : 'Фид: ') + (list.find(item => item.id === owner)?.title || owner);
  }
  async function loadSubscriptions() {
    const operation = subscriptionOperations.begin('load', {abortable:true});
    const result = await api('subscriptions', 'GET', undefined, operation.signal);
    if (!operation.current) return;
    subscriptions = result;
    onFolders(subscriptions.folders);
    const options = [...subscriptions.folders.map(f => ['folder/' + f.id, 'Папка: ' + f.title]), ...subscriptions.feeds.map(f => ['feed/' + f.id, 'Фид: ' + f.title])];
    if (!options.some(([id]) => id === selectedOwner)) selectedOwner = null;
    const tree = byId('feed-tree'); tree.replaceChildren();
    for (const folder of subscriptions.folders) {
      const group = element('div'); group.className = 'feed-group';
      const button = element('button', folder.title); button.dataset.owner = 'folder/' + folder.id;
      button.onclick = () => selectOwner(button.dataset.owner); group.append(button);
      for (const feed of subscriptions.feeds.filter(f => f.folder_id === folder.id)) {
        const child = element('button', feed.title); child.className = 'feed-child'; child.dataset.owner = 'feed/' + feed.id;
        child.onclick = () => selectOwner(child.dataset.owner); group.append(child);
      }
      tree.append(group);
    }
    if (!options.length) tree.append(element('p', 'Фидов пока нет. Добавьте источник.'));
    byId('feed-detail').hidden = !selectedOwner;

  }
  async function selectOwner(path) {
    operations.invalidate();
    reconnect.deactivate();
    selectedOwner = path || null; currentPath = selectedOwner;
    byId('feed-detail').hidden = !selectedOwner;
    byId('settings-editor').hidden = true;
    byId('reconnect-connection').hidden = true;
    if (!selectedOwner) return;
    onDetail();
    await loadSettings();
  }
  async function loadSettings() {
    const operation = operations.begin('load', {abortable:true});
    const path = scopePath();
    if (!path) return;
    const values = draft(path);
    try {
      const result = await api('settings/' + path, 'GET', undefined, operation.signal);
      if (!operation.current) return;
      byId('settings-editor').hidden = false;
      byId('owner-heading').textContent = describeOwner(...path.split('/'));
      byId('feed-options').replaceChildren();
      const feed = subscriptions.feeds.find(f => 'feed/' + f.id === path);
      if (feed) renderFeed(feed);
      const container = byId('setting-fields');
      container.replaceChildren();
      if (path.startsWith('feed/') || path.startsWith('folder/')) {
        const update = element('button', 'Обновить обзор сейчас');
        update.onclick = async () => {
          const action = operations.begin('digest');
          try { await api('digests/' + path, 'POST', {period: true}); action.commit(() => editorMessage('Обзор поставлен в обработку')); onChanged(); }
          catch (error) { reportError(error, 'Интерфейс Око'); action.commit(() => editorMessage(error.message)); }
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
        if (input.id in values) input.value = values[input.id];
        input.oninput = input.onchange = () => {values[input.id] = input.value;};
        const info = element('small', 'Эффективное значение: ' + (type === 'boolean' ? (effective.value ? 'Включено' : 'Выключено') : String(effective.value)) + ' · ' + describeOwner(effective.scope, effective.owner));
        const save = element('button', 'Сохранить поле');
        const reset = element('button', 'Сбросить');
        reset.disabled = !(field in result.overrides);
        async function update(value) {
          const action = operations.begin('field/' + field);
          const submittedDraft = input.value;
          try {
            await api('settings/' + path, 'PATCH', {[field]: value});
            if (values[input.id] === submittedDraft) delete values[input.id];
            await action.commit(loadSettings);
            action.commit(() => editorMessage('Настройка сохранена'));
          } catch (error) { reportError(error, 'Интерфейс Око'); action.commit(() => editorMessage(error.message)); }
        }
        save.onclick = () => update(type === 'boolean' ? input.value === 'true' : type === 'number' ? Number(input.value) : input.value);
        reset.onclick = () => update(null);
        row.append(labelled(title, input), info, save, reset); container.append(row);
      }
    } catch (error) { reportError(error, 'Интерфейс Око'); operation.commit(() => editorMessage(error.message)); }
  }
  function renderHistoryState(history) {
    youtubeHistory?.update(history);
    const progress = byId('history-progress');
    if (progress) {progress.textContent = historyProgress(history); progress.hidden = !history;}
    if (history) byId('history-legacy')?.remove();
    const toggle = byId('history-toggle');
    if (toggle) {
      toggle.hidden = !history || ['done','cancelled'].includes(history.state);
      const resume = history?.paused || history?.state === 'waiting';
      toggle.textContent = resume ? 'Продолжить историю' : 'Приостановить историю';
      toggle.dataset.paused = String(Boolean(resume));
    }
  }
  function renderFeed(feed) {
    youtubeHistory = null;
    const values = draft('feed/' + feed.id);
    const remember = (input, name) => {
      input.id = 'feed-' + name;
      if (input.id in values) {if (input.type === 'checkbox') input.checked = values[input.id]; else input.value = values[input.id];}
      input.oninput = input.onchange = () => {values[input.id] = input.type === 'checkbox' ? input.checked : input.value;};
    };
    const container = byId('feed-options');
    const help = element('p'); help.id = 'source-help'; const capabilities = element('details'); capabilities.append(element('summary', 'Возможности проверки изменений'), help); container.append(capabilities); sourceHelp();
    container.append(element('h2', 'Подключение фида'));
    if (feed.initial_history || (feed.kind === 'youtube' && feed.transport === 'youtube')) {
      const progress = element('p', historyProgress(feed.initial_history)); progress.id = 'history-progress'; progress.dataset.feed = feed.id; progress.setAttribute('role', 'status');
      const toggle = element('button'); toggle.id = 'history-toggle';
      toggle.onclick = async () => {
        const action = operations.begin('history');
        toggle.disabled = true;
        try {
          const history = await api('feeds/' + feed.id + '/history', 'PATCH', {paused: toggle.dataset.paused !== 'true'});
          action.commit(() => renderHistoryState(history));
        } catch (error) {reportError(error, 'Интерфейс Око'); action.commit(() => editorMessage(error.message));}
        finally {toggle.disabled = false;}
      };
      container.append(progress, toggle);
      renderHistoryState(feed.initial_history);
    }
    if (!feed.initial_history && feed.kind !== 'demo') {
      const legacy = element('div'); legacy.id = 'history-legacy';
      container.append(legacy);
      legacy.append(element('p', feed.legacy_since ? `Зафиксированное начало истории: ${new Date(feed.legacy_since * 1000).toLocaleString()}` : Number.isInteger(feed.initial_days) ? `Сохранённая настройка глубины: ${feed.initial_days} дней; фактическая граница не сохранена` : 'Фактическая граница истории не сохранена'),
        element('p', 'Количество и прогресс начальной загрузки не сохранены. Существующая граница и курсор сохраняются; повторная загрузка истории не запускается.'));
    }
    if (feed.kind === 'youtube' && feed.transport === 'youtube') youtubeHistory = createYouTubeHistory({container, feed, api, operations, values, onChanged, reload: async () => {await loadSubscriptions(); await loadSettings();}, message: editorMessage});
    const connection = feed.kind === 'demo' ? 'Тестовый источник подключён' : feed.transport === 'telegram' ? 'Telegram: ' + (feed.telegram_peer ? 'подключение настроено' : 'требуется подключение') : feed.transport === 'rss' ? 'RSS подключён: сбор при открытой вкладке' : feed.transport === 'youtube' ? 'YouTube подключён: сбор при открытой вкладке' : feed.kind === 'youtube' ? 'YouTube отключён' : feed.connection === 'ambiguous' ? 'Адрес неоднозначен: требуется явный выбор подключения' : 'Требуется подключение сборщика';
    container.append(element('p', connection), element('p', 'Исходные адреса: ' + feed.original_urls.join(', ')));
    if (feed.kind === 'youtube') container.append(element('p', (feed.transport === 'youtube' ? 'YouTube: обновления собираются при открытой вкладке. ' : 'YouTube подключается автоматически при добавлении URL фида. ') + 'Тезисы включены по умолчанию; для YouTube запрашиваются доступные субтитры. Высокая важность также включает субтитры и тезисы. Видео не скачивается.'));
    const folder = selectControl(subscriptions.folders.map(f => [f.id, f.title]), feed.folder_id);
    const importance = selectControl(Object.entries(importanceNames), feed.importance);
    const publicAi = element('input'); publicAi.type = 'checkbox'; publicAi.checked = feed.public_ai;
    remember(folder, 'folder'); remember(importance, 'importance'); remember(publicAi, 'public-ai');
    container.append(labelled('Папка', folder), labelled('Важность фида', importance), labelled('Разрешить обработку публичным AI', publicAi));
    let auto, history, transport, youtubeEnabled;
    if (feed.kind === 'telegram') {
      auto = selectControl([['', 'Выберите режим'], ['true', 'Автопрочитывать после полного сохранения'], ['false', 'Отмечать только после «Просмотрено»']], feed.auto_read === null ? '' : feed.auto_read);
      history = element('input'); history.type = 'checkbox'; history.checked = feed.read_history;
      remember(auto, 'auto'); remember(history, 'read-history');
      container.append(element('p', 'Око подтверждает только безопасную границу истории. Просмотр позднего материала может ожидать более ранний.'), labelled('Автопрочитывание в Telegram', auto), labelled('Отметить историю до начала импорта прочитанной', history), element('p', 'Без разрешения очистки старая непрочитанная история может задержать отметки Telegram. Разрешение применяется при работающем Око только к истории до зафиксированного начала импорта.'));
    }
    if (feed.kind === 'youtube') {
      youtubeEnabled = element('input'); youtubeEnabled.type = 'checkbox'; youtubeEnabled.checked = feed.transport === 'youtube';
      remember(youtubeEnabled, 'youtube');
      container.append(labelled('Собирать обновления YouTube', youtubeEnabled));
    }
    if (feed.kind === 'telegram' || feed.kind === 'unresolved') {
      transport = selectControl([['', 'Подключение не выбрано'], ...(feed.kind === 'telegram' ? [['telegram', 'Telegram с локальными медиа']] : []), ['rss', 'Ограниченный RSS (без Telegram-медиа и отметок чтения)']], feed.transport || '');
      remember(transport, 'transport');
      container.append(labelled('Способ сбора', transport));
    }
    const save = element('button', 'Сохранить фид');
    save.onclick = async () => {
      const action = operations.begin('feed');
      const patch = {folder_id: folder.value, importance: importance.value, public_ai: publicAi.checked};
      if (auto) { patch.auto_read = auto.value === '' ? null : auto.value === 'true'; patch.read_history = history.checked; }
      if (transport) patch.transport = transport.value || null;
      if (youtubeEnabled) patch.transport = youtubeEnabled.checked ? 'youtube' : null;
      try {
        const submitted = Object.fromEntries(Object.entries(values).filter(([key]) => key.startsWith('feed-')));
        await api('feeds/' + feed.id, 'PATCH', patch);
        for (const [key, value] of Object.entries(submitted)) if (values[key] === value) delete values[key];
        onChanged();
        await loadSubscriptions();
        await action.commit(loadSettings);
        action.commit(() => editorMessage('Фид сохранён'));
      } catch (error) { reportError(error, 'Интерфейс Око'); action.commit(() => editorMessage(error.message)); }
    };
    container.append(save);
    if (feed.kind === 'telegram') {
      const connect = element('button', 'Настроить подключение Telegram-фида');
      connect.onclick = () => reconnect.configure(feed, subscriptions.folders.find(f => f.id === feed.folder_id).title);
      container.append(connect);
    }
  }

  function deactivate() { operations.invalidate(); currentPath = null; reconnect.deactivate(); }
  return {
    async open(global, preferred) {
      deactivate();
      if (preferred !== undefined) selectedOwner = preferred;
      currentPath = global ? 'oko/global' : selectedOwner;
      byId(global ? 'global-settings' : 'object-settings').append(byId('settings-editor'));
      byId('settings-editor').hidden = true;
      byId('feed-options').hidden = global;
      byId('feed-detail').hidden = !selectedOwner;
      if (!global && selectedOwner) onDetail();
      const action = operations.begin('open');
      await loadSubscriptions();
      await action.commit(loadSettings);
    },
    deactivate,
    refresh: loadSubscriptions,
    sources(status) {sourceStatus = status; sourceHelp();},
    status(histories) {
      const progress = histories.find(history => currentPath === 'feed/' + history.feed_id);
      if (progress) renderHistoryState(progress);
    },
    stop() {deactivate(); subscriptionOperations.invalidate(); reconnect.stop();},
  };
}
