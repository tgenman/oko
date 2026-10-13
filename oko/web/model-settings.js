import {reportError} from './debug.js';
import {createOperations} from './ui-operations.js';
import {element} from './controls.js';

export function createModelSettings({api}) {
  const byId = id => document.getElementById(id);
  const operations = createOperations();
  let stopped = false, dirty = false, loaded = false;
  for (const id of ['llamacpp-endpoint', 'llamacpp-model']) {
    byId(id).oninput = () => {dirty = true; byId('llamacpp-local').checked = false;};
  }
  byId('llamacpp-local').onchange = () => {dirty = true;};
  byId('llamacpp-save').onclick = async () => {
    const action = operations.begin('llamacpp-status');
    try {
      await api('processors/llamacpp', 'PUT', {endpoint:byId('llamacpp-endpoint').value, model:byId('llamacpp-model').value, local_execution:byId('llamacpp-local').checked});
      action.commit(() => {byId('llamacpp-status').textContent = 'Подключение сохранено. Выберите llama.cpp и включите тезисы для Око, папки или фида.';});
    } catch(error) {reportError(error, 'Подключения моделей'); action.commit(() => {byId('llamacpp-status').textContent = error.message;});}
  };
  byId('llamacpp-check').onclick = async () => {
    const action = operations.begin('llamacpp-status');
    byId('llamacpp-status').textContent = 'Проверка…';
    try {
      const result = await api('processors/llamacpp/check', 'POST');
      action.commit(() => {
        byId('llamacpp-status').textContent = result.mode + ' · ' + result.model;
        byId('llamacpp-models').replaceChildren(...result.models.map(name => {const option = element('option'); option.value = name; return option;}));
      });
    } catch(error) {reportError(error, 'Подключения моделей'); action.commit(() => {byId('llamacpp-status').textContent = error.message;});}
  };
  byId('codex-check').onclick = async () => {
    const action = operations.begin('codex');
    const button = byId('codex-check'); button.disabled = true;
    byId('codex-status').textContent = 'Проверка CLI и входа…';
    try {
      const result = await api('processors/codex/check', 'POST');
      action.commit(() => {
        const auth = {chatgpt:'вход через ChatGPT', api_key:'вход через API key', missing:'вход отсутствует', unknown:'способ входа не распознан', unchecked:'вход не проверен'};
        byId('codex-status').textContent = (result.version ? 'CLI ' + result.version + ' · ' : '') + auth[result.auth] + (result.limits_checked ? '. Лимиты проверены. ' : '. Лимиты не проверены. ') + (result.available ? 'Изолированный запуск доступен.' : result.reason);
      });
    } catch(error) {reportError(error, 'Подключения моделей'); action.commit(() => {byId('codex-status').textContent = error.message;});}
    finally {action.commit(() => {button.disabled = stopped;});}
  };
  function deactivate() {
    operations.invalidate();
    byId('codex-check').disabled = stopped;
    for (const id of ['codex-status', 'llamacpp-status']) byId(id).textContent = '';
  }
  return {
    async open() {
      if (loaded) return;
      const action = operations.begin('load', {abortable:true});
      try {
        const config = await api('processors/llamacpp', 'GET', undefined, action.signal);
        action.commit(() => {
          if (config && !dirty) {byId('llamacpp-endpoint').value = config.endpoint; byId('llamacpp-model').value = config.model; byId('llamacpp-local').checked = config.local_execution;}
          loaded = true;
        });
      } catch(error) {reportError(error, 'Подключения моделей'); action.commit(() => {byId('llamacpp-status').textContent = error.message;});}
    },
    deactivate,
    stop() {stopped = true; deactivate();},
  };
}
