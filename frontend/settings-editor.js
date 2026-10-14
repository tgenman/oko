import {treeButton, treeTitle, treeChevron, folderFeeds, feedIcon} from './feed-tree-view.js';
import {historyProgress} from './initial-history.js';
import {reportError} from './debug.js';
import {createOperations} from './ui-operations.js';
import {element, selectControl, labelled} from './controls.js';
import {createTelegramConnection} from './telegram-connection.js';
import {createYouTubeHistory} from './youtube-history.js';

export function createSettingsEditor({api, onChanged, onDetail, onFolders, onNavigate, onRendered}) {
  const byId = id => document.getElementById(id);
  const reconnect = createTelegramConnection({api, prefix: 'reconnect', context: () => operations.capture(), onConnected: async (_feed, action) => {onChanged(); await loadSubscriptions(); await action.commit(loadSettings);}});
  const fields = {
    events: ['Объединять события', 'boolean'], event_hours: ['Окно события, часов', 'number'],
    originals: ['Показывать исходную публикацию', 'boolean'], theses: ['Тезисы публикаций', 'boolean'],
    digests: ['Обзоры за период', 'boolean'], prompt_theses: ['Промпт тезисов', 'text'],
    prompt_digest: ['Промпт обзора', 'text'], prompt_filters: ['Правила фильтрации', 'text'],
    schedule: ['Расписание обзора', 'string'],
    digest_days: ['Период обзора, дней', 'number'], processor: ['Обработчик', 'processor']
  };
  const importanceNames = {normal: 'Обычная', elevated: 'Повышенная', high: 'Высокая'};
  let subscriptions = {folders: [], feeds: []};
  let sourceStatus = [];
  const collapsed = new Set();
  function sourceHelp() {
    const target = document.getElementById('source-help');
    if (target) {target.textContent = sourceStatus.find(s => currentPath === 'feed/' + s.feed_id)?.changes || ''; target.parentElement.hidden = !target.textContent;}
  }
  let selectedOwner = null, currentPath = null;
  let youtubeHistory = null;
  const drafts = new Map();
  const needsRefresh = new Set();
  let linkId = 0;
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
    renderSubscriptions();
    byId('feed-detail').hidden = !selectedOwner;

  }
  function renderSubscriptions() {
    const tree = byId('feed-tree');
    const focus = document.activeElement?.dataset.treeKey, scroll = byId('feed-list').scrollTop;
    const groups = [];
    for (const folder of subscriptions.folders) {
      const group = element('div'); group.className = 'feed-group';
      const row = element('div'); row.className = 'tree-row folder-row';
      const button = treeButton(folder.title, 'tree-select'); button.dataset.owner = 'folder/' + folder.id;
      button.dataset.treeKey = button.dataset.owner; treeTitle(button, folder.title, 'folder');
      button.onclick = () => selectOwner(button.dataset.owner);
      const children = element('div'); children.id = 'settings-folder-' + folder.id; children.hidden = collapsed.has(folder.id);
      const toggle = treeButton((children.hidden ? 'Раскрыть ' : 'Свернуть ') + folder.title, 'tree-collapse');
      toggle.dataset.treeKey = 'collapse/' + folder.id;
      toggle.setAttribute('aria-expanded', String(!children.hidden)); toggle.setAttribute('aria-controls', children.id);
      toggle.append(treeChevron());
      toggle.onclick = () => {if (collapsed.has(folder.id)) collapsed.delete(folder.id); else collapsed.add(folder.id); renderSubscriptions();};
      row.append(button, toggle); group.append(row, children);
      for (const feed of folderFeeds(subscriptions, folder.id)) {
        const childRow = element('div'); childRow.className = 'tree-row feed-row';
        const child = treeButton(feed.title, 'tree-select feed-child'); child.dataset.owner = 'feed/' + feed.id;
        child.dataset.treeKey = child.dataset.owner; treeTitle(child, feed.title, feedIcon(feed)); child.append(treeChevron());
        child.onclick = () => selectOwner(child.dataset.owner); childRow.append(child); children.append(childRow);
      }
      groups.push(group);
    }
    tree.replaceChildren(...groups);
    if (!groups.length) tree.append(element('p', 'Фидов пока нет. Добавьте источник.'));
    for (const button of tree.querySelectorAll('[data-owner]')) button.setAttribute('aria-pressed', String(button.dataset.owner === selectedOwner));
    byId('feed-list').scrollTop = scroll;
    if (focus) [...tree.querySelectorAll('[data-tree-key]')].find(node => node.dataset.treeKey === focus)?.focus({preventScroll:true});
  }
  function focusOwner() {
    const buttons = [...byId('feed-tree').querySelectorAll('[data-owner]')];
    const visible = owner => buttons.find(node => node.dataset.owner === owner && !node.closest('[hidden]'));
    const feed = subscriptions.feeds.find(item => 'feed/' + item.id === selectedOwner);
    const target = visible(selectedOwner) || (feed && visible('folder/' + feed.folder_id)) || buttons[0];
    target?.focus({preventScroll:true});
  }
  async function selectOwner(path) {
    operations.invalidate();
    reconnect.deactivate();
    selectedOwner = path || null; currentPath = selectedOwner;
    byId('feed-detail').hidden = !selectedOwner;
    byId('settings-editor').hidden = true;
    byId('reconnect-connection').hidden = true;
    if (!selectedOwner) return;
    renderSubscriptions();
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
      const focused = document.activeElement;
      const focusedId = focused?.id, focusedTarget = focused?.dataset.settingsTarget;
      const focusedPage = focused?.closest('[data-settings-page]')?.dataset.settingsPage;
      byId('settings-editor').hidden = false;
      byId('owner-heading').textContent = describeOwner(...path.split('/'));
      byId('feed-options').replaceChildren();
      const feed = subscriptions.feeds.find(f => 'feed/' + f.id === path);
      if (feed) renderFeed(feed);
      needsRefresh.delete(path);
      renderFields(result, path, values);
      onRendered();
      const restore = focusedId ? byId(focusedId) : focusedTarget ? [...byId('setting-fields').querySelectorAll('[data-settings-target]')].find(node => node.dataset.settingsTarget === focusedTarget && node.closest('[data-settings-page]')?.dataset.settingsPage === focusedPage) : null;
      if (restore && !restore.closest('[hidden]')) restore.focus({preventScroll:true});
    } catch (error) { reportError(error, 'Интерфейс Око'); operation.commit(() => editorMessage(error.message)); }
  }
  function settingPage(name, heading) {
    const panel = element('div'); panel.dataset.settingsPage = name;
    const title = element('h2', heading); title.className = 'settings-page-title'; title.id = 'settings-page-' + name.replaceAll('/','-'); title.tabIndex = -1; panel.append(title);
    return panel;
  }
  function settingLink(title, value, page, color = '', symbol = '') {
    const button = element('button'); button.className = 'settings-link';
    button.setAttribute('aria-label', title); button.dataset.settingsTarget = page;
    if (symbol) {const icon = element('span', symbol); icon.className = 'settings-icon ' + color; icon.setAttribute('aria-hidden','true'); button.append(icon);}
    const label = element('span', title); label.className = 'settings-label';
    const current = element('span', value); current.className = 'settings-value';
    current.id = 'settings-value-' + (++linkId); if (value) button.setAttribute('aria-describedby',current.id);
    const arrow = element('span', '›'); arrow.setAttribute('aria-hidden','true');
    button.append(label, current, arrow);
    button.onclick = () => onNavigate(page, button);
    return button;
  }
  function fieldValue(field, value) {
    if (fields[field][1] === 'boolean') return value ? 'Включено' : 'Выключено';
    if (field === 'schedule') return value === 'off' ? 'Выключено' : value;
    if (field === 'processor') return value === 'codex' ? 'Codex' : value === 'llamacpp' ? 'llama.cpp' : value;
    if (fields[field][1] === 'text') return value ? 'Настроен' : 'Не задан';
    return String(value);
  }
  function renderFields(result, path, values) {
    const container = byId('setting-fields'); container.replaceChildren();
    const home = settingPage('home', 'Настройки'); container.append(home);
    if (path.startsWith('feed/')) {
      const group = element('div'); group.className = 'settings-group';
      group.append(settingLink('Подключение и параметры', '', 'feed-connection', 'gray', '▣')); home.append(group);
    }
    const groups = [
      ['events', 'Объединение событий', ['events','event_hours'], 'orange', '▤'],
      ['reading', 'Чтение', ['originals','theses'], 'green', '▥'],
      ['digests', 'Обзоры за период', ['digests','schedule','digest_days','prompt_digest'], 'purple', '◷'],
      ['filters', 'Фильтрация', ['prompt_filters'], '', '▽'],
      ['prompts', 'Промпты', ['prompt_theses','prompt_digest'], 'purple', '☷'],
      ['processor', 'Обработчик', ['processor'], '', '▣'],
    ];
    let homeGroup;
    for (const [name, title, keys, color, symbol] of groups) {
      if (name === 'events' && path.startsWith('feed/')) continue;
      if (name === 'events' || name === 'reading' && path.startsWith('feed/') || name === 'prompts') {
        const label = element('h3', name === 'prompts' ? 'Обработка' : 'Материалы'); label.className = 'settings-group-title'; home.append(label);
        homeGroup = element('div'); homeGroup.className = 'settings-group'; home.append(homeGroup);
      }
      const value = name === 'filters' ? (result.effective.prompt_filters.value ? 'Настроена' : 'Выключена') : ['events','digests','processor'].includes(name) ? fieldValue(keys[0],result.effective[keys[0]].value) : '';
      homeGroup.append(settingLink(title, value, name, color, symbol));
      const panel = settingPage(name, title), list = element('div'); list.className = 'settings-group'; panel.append(list);
      for (const key of keys) list.append(settingLink(fields[key][0], fieldValue(key,result.effective[key].value), 'field/' + key));
      if (name === 'reading') list.append(settingLink('Промпт тезисов', '', 'field/prompt_theses'));
      if (name === 'digests') panel.append(element('p', 'Расписание действует только при работающем Око.'));
      container.append(panel);
    }
    if (!path.startsWith('oko/')) {
      const update = element('button', 'Обновить обзор сейчас'); update.className = 'settings-action';
      update.onclick = async () => {
        const action = operations.begin('digest');
        try {await api('digests/' + path, 'POST', {period:true}); action.commit(() => editorMessage('Обзор поставлен в обработку')); onChanged();}
        catch (error) {reportError(error, 'Интерфейс Око'); action.commit(() => editorMessage(error.message));}
      }; home.append(update);
    }
    for (const [field, [title, type]] of Object.entries(fields)) {
      if (path.startsWith('feed/') && ['events','event_hours'].includes(field)) continue;
      const effective = result.effective[field], panel = settingPage('field/' + field, title);
      const row = element('form'); row.className = 'setting-row settings-group';
      let input, enabled;
      const initial = 'setting-' + field in values ? values['setting-' + field] : String(effective.value);
      if (type === 'boolean') {input = element('input'); input.type = 'checkbox'; input.setAttribute('role','switch'); input.checked = initial === 'true';}
      else if (type === 'processor') input = selectControl([['codex','Codex'],['llamacpp','llama.cpp'], ...(!['codex','llamacpp'].includes(effective.value) ? [[effective.value,effective.value]] : [])], initial);
      else if (type === 'text') {input = element('textarea'); input.rows = 8; input.value = initial;}
      else {input = element('input'); input.type = field === 'schedule' ? 'time' : 'number'; input.required = true;
        if (field === 'schedule') {
          enabled = element('input'); enabled.type = 'checkbox'; enabled.setAttribute('role','switch'); enabled.checked = initial !== 'off';
          input.value = initial === 'off' ? '09:00' : initial; input.disabled = !enabled.checked;
          row.append(labelled('Каждый день', enabled));
        } else {input.min = '1'; input.max = field === 'event_hours' ? '87600' : '3650'; input.step = '1'; input.value = initial;}
      }
      input.id = 'setting-' + field;
      const currentValue = () => type === 'boolean' ? String(input.checked) : field === 'schedule' && !enabled.checked ? 'off' : input.value;
      const saved = element('details'); saved.append(element('summary','Сохранённое значение'), element('p', type === 'text' ? String(effective.value) || 'Пустое поле' : fieldValue(field,effective.value)));
      const showDraft = () => {saved.hidden = currentValue() === String(effective.value);};
      const remember = () => {values[input.id] = currentValue(); showDraft();}; input.oninput = input.onchange = remember;
      if (enabled) enabled.onchange = () => {input.disabled = !enabled.checked; remember();};
      const label = labelled(field === 'schedule' ? 'Местное время' : title, input); label.className = 'settings-control';
      const info = element('small', (field in result.overrides ? 'Своё значение · ' : 'Наследуется · ') + describeOwner(effective.scope,effective.owner));
      if (path.startsWith('oko/') && !(field in result.overrides)) info.textContent = describeOwner(effective.scope,effective.owner);
      const save = element('button', 'Сохранить'); save.id = 'setting-save-' + field; save.type = 'submit'; save.className = 'settings-action';
      const reset = element('button', 'Сбросить'); reset.id = 'setting-reset-' + field; reset.type = 'button'; reset.className = 'settings-action'; reset.disabled = !(field in result.overrides);
      const discard = element('button', 'Отменить изменение'); discard.id = 'setting-discard-' + field; discard.type = 'button'; discard.className = 'settings-action';
      discard.onclick = async () => {
        const action = operations.begin('discard/' + field);
        delete values[input.id];
        if (type === 'boolean') input.checked = Boolean(effective.value); else input.value = String(effective.value);
        if (enabled) {enabled.checked = effective.value !== 'off';input.disabled = !enabled.checked;}
        showDraft();
        try {await api.discardDraft?.('settings/' + path, field);} catch(error) {reportError(error,'Отмена изменения');action.commit(() => editorMessage(error.message));}
      };
      let saving = false;
      async function update(value) {
        if (saving) return;
        saving = true; save.disabled = reset.disabled = true;
        editorMessage('Сохранение…');
        const action = operations.begin('field/' + field), submittedDraft = currentValue();
        try {
          await api('settings/' + path,'PATCH',{[field]:value});
          if (values[input.id] === submittedDraft) delete values[input.id];
          needsRefresh.add(path);
          if (path === scopePath()) await loadSettings();
          action.commit(() => editorMessage('Настройка сохранена'));
        } catch (error) {reportError(error,'Интерфейс Око'); action.commit(() => editorMessage(error.message));}
        finally {saving = false; save.disabled = false; reset.disabled = !(field in result.overrides);}
      }
      row.onsubmit = event => {event.preventDefault(); if (row.reportValidity()) void update(type === 'boolean' ? input.checked : type === 'number' ? Number(input.value) : currentValue());};
      reset.onclick = () => update(null);
      showDraft(); row.append(label, info, saved, save, reset, discard); panel.append(row);
      if (field === 'prompt_filters') panel.append(element('p','Пустое поле выключает фильтрацию. Скрытые материалы остаются доступны.'));
      if (field === 'schedule') panel.append(element('p','Расписание действует только при работающем Око.'));
      container.append(panel);
    }
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
        needsRefresh.add('feed/' + feed.id);
        if (scopePath() === 'feed/' + feed.id) await loadSettings();
        action.commit(() => editorMessage('Фид сохранён'));
      } catch (error) { reportError(error, 'Интерфейс Око'); action.commit(() => editorMessage(error.message)); }
    };
    container.append(save);
    if (feed.kind === 'telegram') {
      const connect = element('button', 'Настроить подключение Telegram-фида');
      connect.onclick = () => {onNavigate('reconnect', connect); void reconnect.configure(feed, subscriptions.folders.find(f => f.id === feed.folder_id).title);};
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
    focusOwner,
    viewChanged() {operations.invalidate(); reconnect.deactivate(); editorMessage('');},
    refreshView() {if (needsRefresh.has(scopePath())) return loadSettings();},
    refresh: loadSubscriptions,
    sources(status) {sourceStatus = status; sourceHelp();},
    status(histories) {
      const progress = histories.find(history => currentPath === 'feed/' + history.feed_id);
      if (progress) renderHistoryState(progress);
    },
    stop() {deactivate(); subscriptionOperations.invalidate(); reconnect.stop();},
  };
}
