import {createOperations} from './ui-operations.js';
import {element, selectControl, labelled} from './controls.js';
import {reportError} from './debug.js';

export function createOpmlImport({api, context, onImported}) {
  const byId = id => document.getElementById(id);
  const operations = createOperations();
  let text = null, choices = {};
  function conflicts(items) {
    for (const conflict of items) {
      const select = selectControl([['', 'Выберите одну папку'], ...conflict.folders.map(f => [f,f])], choices[conflict.identity] || '');
      select.onchange = () => {choices[conflict.identity] = select.value;};
      byId('import-plan').append(labelled(conflict.identity + ' встречается в нескольких папках', select));
    }
  }
  byId('opml-file').onchange = async () => {
    operations.invalidate();
    const action = operations.begin('preview', {abortable:true}), screen = context();
    const file = byId('opml-file').files[0];
    text = null; choices = {}; byId('import-confirm').hidden = true;
    if (!file) return;
    try {
      if (file.size > 2000000) throw new Error('OPML больше 2 МБ');
      const content = await file.text();
      const plan = await api('import/preview', 'POST', {opml:content}, action.signal);
      action.commit(() => screen.commit(() => {
        text = content;
        byId('import-plan').replaceChildren(element('p', `Папок: ${plan.folders.length}; источников: ${plan.feeds.length}. Повторяющиеся адреса объединены. Новые фиды: сутки истории, публичный AI и очистка истории выключены.`));
        conflicts(plan.feeds.filter(f=>f.folders.length>1));
        byId('import-confirm').hidden = false;
      }));
    } catch(error) {
      reportError(error, 'Импорт OPML');
      action.commit(() => screen.commit(() => {byId('add-message').textContent = error.message;}));
    }
  };
  byId('import-confirm').onclick = async () => {
    if (!text) return;
    const action = operations.begin('import'), screen = context();
    const submitted = text;
    try {
      await api('import', 'POST', {opml:submitted, choices});
      action.commit(() => {
        text = null; choices = {}; byId('opml-file').value = ''; byId('import-confirm').hidden = true;
        byId('import-plan').replaceChildren(element('p', 'Подписки импортированы. RSS и YouTube подключены автоматически; для Telegram настройте подключение.'));
      });
      await onImported({commit: effect => action.commit(() => screen.commit(effect))});
    } catch(error) {
      reportError(error, 'Импорт OPML');
      action.commit(() => screen.commit(() => {
        if (error.detail?.conflicts) {byId('import-plan').replaceChildren(element('p','Выберите одну папку для каждого конфликта и повторите импорт.')); conflicts(error.detail.conflicts);}
        else byId('add-message').textContent = error.message;
      }));
    }
  };
  return {stop: () => operations.invalidate()};
}
