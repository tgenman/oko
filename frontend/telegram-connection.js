import {bindInitialHistory} from './initial-history.js';
import {reportError} from './debug.js';
export function createTelegramConnection({api, onConnected, prefix = 'telegram', folderPicker, context = () => ({current:true, commit: effect => effect()})}) {
  const byId = id => document.getElementById(id.replace(/^telegram-/, prefix + '-'));
  const account = byId('telegram-account'), peer = byId('telegram-peer');
  const folder = byId('telegram-folder'), topic = byId('telegram-topic');
  const add = byId('telegram-add'), accounts = byId('telegram-accounts');
  const topics = byId('telegram-topics'), message = byId('telegram-message');
  const folderValue = () => folderPicker ? folderPicker.value() : folder.value.trim();
  const initialHistory = bindInitialHistory(prefix);
  const historyFields = byId('telegram-history');
  const inputs = [account, peer, folder, topic, byId('telegram-auto')];
  let busy = false, loadingAccounts = false, loadingTopics = false, stopped = false;
  let generation = 0;
  let feedId = null, accountsTask = null, configureGeneration = 0;
  const drafts = new Map();
  function capture() {
    if (!feedId || busy || byId('telegram-connection').hidden) return;
    drafts.set(feedId, {account: account.value, peer: peer.value, folder: folder.value, auto: byId('telegram-auto').checked,
      topic: topic.value, options: Array.from(topic.options, o => [o.value, o.textContent])});
  }
  function deactivate() {capture(); configureGeneration++; byId('telegram-connection').hidden = true;}

  const validPeer = () => /^(@?[a-zA-Z][a-zA-Z0-9_]{3,31}|-[0-9]+)$/.test(peer.value.trim());
  function update() {
    add.disabled = stopped || busy || loadingAccounts || loadingTopics || !account.value || !validPeer() || !folderValue() || (topic.options.length > 0 && !topic.value);
    accounts.disabled = stopped || busy || loadingAccounts;
    topics.disabled = stopped || busy || loadingAccounts || loadingTopics || !account.value || !validPeer();
    for (const input of inputs) input.disabled = stopped || busy || (input === account && loadingAccounts) || (input === topic && !topic.options.length) || (input === folder && feedId !== null);
    if (folderPicker) folderPicker.setDisabled(stopped || busy);
    historyFields.disabled = stopped || busy;
    historyFields.hidden = feedId !== null;
    add.textContent = busy ? 'Подключение…' : 'Подключить источник';
  }
  function option(value, title) {
    const node = document.createElement('option'); node.value = value; node.textContent = title; return node;
  }
  function resetTopics() {
    generation++; loadingTopics = false;
    topic.replaceChildren(); byId('telegram-topic-label').hidden = true;
    update();
  }
  async function loadAccounts() {
    if (stopped || busy) return;
    const screen = context();
    loadingAccounts = true; resetTopics();
    const previous = account.value;
    account.replaceChildren(option('', 'Загрузка аккаунтов…')); update();
    try {
      // Share the data request, but let each caller render into its own context.
      if (!accountsTask) accountsTask = api('telegram/accounts').finally(() => {accountsTask = null;});
      const names = await accountsTask;
      if (stopped || !screen.current) return;
      account.replaceChildren(option('', names.length ? 'Выберите аккаунт' : 'Нет доступных аккаунтов'), ...names.map(name => option(name, name)));
      if (names.includes(previous)) account.value = previous;
      else if (names.length === 1) account.value = names[0];
      message.textContent = names.length ? '' : 'В клиенте Telegram нет настроенных аккаунтов.';
    } catch (error) { reportError(error, 'telegram-connection.js');
      if (!stopped) screen.commit(() => {account.replaceChildren(option('', 'Аккаунты недоступны')); message.textContent = error.message;});
    } finally { loadingAccounts = false; update(); }
  }
  account.onchange = resetTopics;
  peer.oninput = resetTopics;
  if (folderPicker) folderPicker.onChange(update);
  else folder.oninput = update;
  topic.onchange = update;
  accounts.onclick = loadAccounts;
  topics.onclick = async () => {
    if (topics.disabled) return;
    resetTopics(); loadingTopics = true;
    const stamp = generation, screen = context();
    update(); message.textContent = 'Загрузка тем…';
    try {
      const result = await api('telegram/topics', 'POST', {account: account.value, peer: peer.value.trim()});
      if (stopped || stamp !== generation || !screen.current) return;
      topic.replaceChildren(...(result.length ? [option('', 'Выберите тему'), ...result.map(item => option(String(item.id), item.title))] : []));
      byId('telegram-topic-label').hidden = !result.length;
      message.textContent = result.length ? 'Выберите тему форумной группы.' : 'Это канал или обычная группа — тему выбирать не нужно.';
    } catch (error) { reportError(error, 'telegram-connection.js'); if (!stopped && stamp === generation) screen.commit(() => {message.textContent = error.message;}); }
    finally { if (stamp === generation) {loadingTopics = false; update();} }
  };
  add.onclick = async () => {
    if (add.disabled || (folderPicker && !folderPicker.valid())) return;
    const history = feedId ? null : initialHistory();
    if (!feedId && !history) return;
    const screen = context();
    busy = true; update(); message.textContent = 'Подключение…';
    try {
      const feed = await api('telegram/connect', 'POST', {account: account.value, peer: peer.value.trim(), folder: folderValue(), auto_read: byId('telegram-auto').checked, topic_id: topic.value ? Number(topic.value) : null, ...(feedId ? {feed_id: feedId} : {initial_history: history})});
      if (stopped) return;
      if (feedId) drafts.delete(feedId);
      peer.value = ''; feedId = null; if (folderPicker) folderPicker.reset(); else folder.value = 'Telegram'; byId('telegram-auto').checked = false;
      byId('telegram-history-mode').value = 'count'; byId('telegram-history-mode').dispatchEvent(new Event('change'));
      byId('telegram-history-count').value = '20'; byId('telegram-history-date').value = '';
      byId('telegram-new').hidden = true; resetTopics();
      screen.commit(() => {message.textContent = 'Источник подключён. Сбор начался; отметки прочитанного синхронизируются по настройкам фида.';});
      await onConnected(feed, screen);
    } catch (error) { reportError(error, 'telegram-connection.js'); if (!stopped) screen.commit(() => {message.textContent = error.message;}); }
    finally {busy = false; update();}
  };
  byId('telegram-new').onclick = () => {
    if (busy || stopped) return;
    feedId = null; peer.value = ''; if (folderPicker) folderPicker.reset(); else folder.value = 'Telegram';
    byId('telegram-new').hidden = true; message.textContent = ''; resetTopics();
  };
  update();
  return {loadAccounts, deactivate, async configure(feed, folderTitle) {
    if (busy || stopped) return;
    capture();
    const stamp = ++configureGeneration;
    await loadAccounts();
    if (busy || stopped || stamp !== configureGeneration) return;
    feedId = feed.id;
    peer.value = feed.telegram_peer || feed.identity.replace(/^telegram:/, '@');
    account.value = feed.telegram_account || account.value;
    folder.value = folderTitle;
    byId('telegram-auto').checked = feed.auto_read === true;
    resetTopics();
    if (feed.telegram_topic) {
      topic.replaceChildren(option(String(feed.telegram_topic), feed.title));
      byId('telegram-topic-label').hidden = false;
    }
    const saved = drafts.get(feed.id);
    if (saved) {account.value = saved.account; peer.value = saved.peer; folder.value = saved.folder; byId('telegram-auto').checked = saved.auto; topic.replaceChildren(...saved.options.map(([value,title]) => option(value,title))); topic.value = saved.topic; byId('telegram-topic-label').hidden = !topic.options.length;}
    byId('telegram-new').hidden = true;
    byId('telegram-connection').hidden = false;
    byId('telegram-connection').scrollIntoView({block: 'start'});
    message.textContent = 'Подключение фида: ' + feed.title;
    update();
  }, stop() {stopped = true; generation++; update();}};
}
