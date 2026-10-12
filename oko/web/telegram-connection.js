import {reportError} from './debug.js';
export function createTelegramConnection({api, onConnected}) {
  const byId = id => document.getElementById(id);
  const account = byId('telegram-account'), peer = byId('telegram-peer');
  const folder = byId('telegram-folder'), topic = byId('telegram-topic');
  const add = byId('telegram-add'), accounts = byId('telegram-accounts');
  const topics = byId('telegram-topics'), message = byId('telegram-message');
  const inputs = [account, peer, folder, topic, byId('telegram-auto')];
  let busy = false, loadingAccounts = false, loadingTopics = false, stopped = false;
  let generation = 0;
  let feedId = null, accountsTask = null;
  const validPeer = () => /^(@?[a-zA-Z][a-zA-Z0-9_]{3,31}|-[0-9]+)$/.test(peer.value.trim());
  function update() {
    add.disabled = stopped || busy || loadingAccounts || loadingTopics || !account.value || !validPeer() || !folder.value.trim() || (topic.options.length > 0 && !topic.value);
    accounts.disabled = stopped || busy || loadingAccounts;
    topics.disabled = stopped || busy || loadingAccounts || loadingTopics || !account.value || !validPeer();
    for (const input of inputs) input.disabled = stopped || busy || (input === account && loadingAccounts) || (input === topic && !topic.options.length) || (input === folder && feedId !== null);
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
  function loadAccounts() {
    if (accountsTask) return accountsTask;
    accountsTask = fetchAccounts().finally(() => {accountsTask = null;});
    return accountsTask;
  }
  async function fetchAccounts() {
    if (stopped || busy) return;
    loadingAccounts = true; resetTopics();
    const previous = account.value;
    account.replaceChildren(option('', 'Загрузка аккаунтов…')); update();
    try {
      const names = await api('telegram/accounts');
      if (stopped) return;
      account.replaceChildren(option('', names.length ? 'Выберите аккаунт' : 'Нет доступных аккаунтов'), ...names.map(name => option(name, name)));
      if (names.includes(previous)) account.value = previous;
      else if (names.length === 1) account.value = names[0];
      message.textContent = names.length ? '' : 'В клиенте Telegram нет настроенных аккаунтов.';
    } catch (error) { reportError(error, 'telegram-connection.js');
      if (!stopped) { account.replaceChildren(option('', 'Аккаунты недоступны')); message.textContent = error.message; }
    } finally { loadingAccounts = false; update(); }
  }
  account.onchange = resetTopics;
  peer.oninput = resetTopics;
  folder.oninput = update;
  topic.onchange = update;
  accounts.onclick = loadAccounts;
  topics.onclick = async () => {
    if (topics.disabled) return;
    resetTopics(); loadingTopics = true;
    const stamp = generation;
    update(); message.textContent = 'Загрузка тем…';
    try {
      const result = await api('telegram/topics', 'POST', {account: account.value, peer: peer.value.trim()});
      if (stopped || stamp !== generation) return;
      topic.replaceChildren(...(result.length ? [option('', 'Выберите тему'), ...result.map(item => option(String(item.id), item.title))] : []));
      byId('telegram-topic-label').hidden = !result.length;
      message.textContent = result.length ? 'Выберите тему форумной группы.' : 'Это канал или обычная группа — тему выбирать не нужно.';
    } catch (error) { reportError(error, 'telegram-connection.js'); if (!stopped && stamp === generation) message.textContent = error.message; }
    finally { if (stamp === generation) {loadingTopics = false; update();} }
  };
  add.onclick = async () => {
    if (add.disabled) return;
    busy = true; update(); message.textContent = 'Подключение…';
    try {
      const feed = await api('telegram/connect', 'POST', {account: account.value, peer: peer.value.trim(), folder: folder.value.trim(), auto_read: byId('telegram-auto').checked, topic_id: topic.value ? Number(topic.value) : null, ...(feedId ? {feed_id: feedId} : {})});
      if (stopped) return;
      peer.value = ''; feedId = null; byId('telegram-new').hidden = true; resetTopics();
      message.textContent = 'Источник подключён. Сбор начался; отметки прочитанного синхронизируются по настройкам фида.';
      await onConnected(feed);
    } catch (error) { reportError(error, 'telegram-connection.js'); if (!stopped) message.textContent = error.message; }
    finally {busy = false; update();}
  };
  byId('telegram-new').onclick = () => {
    if (busy || stopped) return;
    feedId = null; peer.value = ''; folder.value = 'Telegram';
    byId('telegram-new').hidden = true; message.textContent = ''; resetTopics();
  };
  update();
  return {loadAccounts, async configure(feed, folderTitle) {
    if (busy || stopped) return;
    await loadAccounts();
    if (busy || stopped) return;
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
    byId('telegram-new').hidden = false;
    byId('telegram-connection').open = true;
    byId('telegram-connection').scrollIntoView({block: 'start'});
    message.textContent = 'Подключение фида: ' + feed.title;
    update();
  }, stop() {stopped = true; generation++; update();}};
}
