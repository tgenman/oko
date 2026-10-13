import {createOperations} from './ui-operations.js';
import {reportError} from './debug.js';

export function createYouTubeSettings({api}) {
  const byId = id => document.getElementById('youtube-' + id);
  const operations = createOperations();
  const fields = ['mode', 'address', 'username', 'password', 'clear'];
  const dirty = new Set();
  let stopped = false, saving = false, loaded = false;
  byId('save').disabled = true;
  const value = key => key === 'clear' ? byId(key).checked : byId(key).value;
  function proxyFields() {byId('proxy-fields').hidden = value('mode') !== 'proxy';}
  for (const key of fields) byId(key).addEventListener('input', () => {dirty.add(key); if (key === 'mode') proxyFields();});
  byId('mode').addEventListener('change', proxyFields);
  function fill(config) {
    for (const key of ['mode', 'address']) if (!dirty.has(key)) byId(key).value = config[key];
    byId('username').placeholder = config.has_username ? 'Логин сохранён' : 'Не задан';
    byId('password').placeholder = config.has_password ? 'Пароль сохранён' : 'Не задан';
    proxyFields();
  }
  function status(config) {
    if (!config) return;
    byId('short-status').textContent = config.paused ? 'Приостановлено' : config.mode === 'proxy' ? 'Через прокси' : 'Напрямую';
    byId('connection').textContent = config.mode === 'proxy' ? 'Прокси для субтитров: ' + config.address : 'Прямое подключение';
    byId('pause').textContent = config.paused
      ? 'Получение субтитров приостановлено. ' + config.reason + '. Следующая попытка не раньше ' + new Date(config.next_at * 1000).toLocaleString()
      : 'Получение субтитров: не чаще одной загрузки в 10 секунд.';
  }
  byId('save').onclick = async () => {
    if (saving || stopped || !loaded) return;
    saving = true; byId('save').disabled = true;
    operations.begin('load');
    const action = operations.begin('save');
    const sent = Object.fromEntries(fields.map(key => [key, value(key)]));
    const body = {mode:sent.mode, address:sent.address, clear_credentials:sent.clear};
    for (const key of ['username', 'password']) if (dirty.has(key) && sent[key]) body[key] = sent[key];
    try {
      const config = await api('youtube', 'PUT', body);
      // Reconcile the completed write even after closing the form; preserve edits
      // typed during the request. A late reply cannot replace visible status.
      for (const key of fields) if (value(key) === sent[key]) {
        dirty.delete(key);
        if (key === 'clear') byId(key).checked = false;
        else if (key === 'username' || key === 'password') byId(key).value = '';
      }
      action.commit(() => {fill(config); status(config); byId('message').textContent = 'Настройки сохранены';});
    } catch (error) {
      reportError(error, 'Подключение YouTube');
      action.commit(() => {byId('message').textContent = error.message;});
    } finally {
      saving = false; byId('save').disabled = stopped;
    }
  };
  return {
    async open() {
      if (saving) return;
      const action = operations.begin('load', {abortable:true});
      try {
        const config = await api('youtube', 'GET', undefined, action.signal);
        action.commit(() => {fill(config); status(config); loaded = true; byId('save').disabled = stopped;});
      } catch (error) {reportError(error, 'Подключение YouTube'); action.commit(() => {byId('message').textContent = error.message;});}
    },
    status,
    deactivate() {operations.invalidate(); byId('message').textContent = '';},
    stop() {stopped = true; operations.invalidate(); byId('save').disabled = true;},
  };
}
