import {createOperations} from './ui-operations.js';
import {element} from './controls.js';

const kinds = {filter:'Фильтрация',theses:'Тезисы публикации',events:'Сводки событий',digest:'Обзоры за период'};

export function createCodexSettings({api}) {
  const byId = id => document.getElementById(id);
  const operations = createOperations();
  let draft = null, models = [], loaded = false, revision = 0, saving = false;
  function option(value, label = value) {const node = element('option', label); node.value = value; return node;}
  function error() {
    if (!draft) return 'Загрузите каталог и выберите модель для каждого действия.';
    for (const [kind, title] of Object.entries(kinds)) {
      const choice = draft[kind], model = models.find(m => m.model === choice.model);
      if (!model) return title + ': модель отсутствует в каталоге.';
      if (!model.reasoning_efforts.includes(choice.reasoning_effort)) return title + ': модель не поддерживает выбранное усилие рассуждения.';
      if (choice.fast && !model.fast_supported) return title + ': модель не поддерживает Fast.';
    }
    return '';
  }
  function validate() {
    const message = error();
    byId('codex-save').disabled = saving || !!message;
    byId('codex-choice-status').textContent = message || 'Выбор для новой работы. Готовые результаты сохраняются.';
  }
  function render() {
    byId('codex-choices').replaceChildren();
    for (const [kind, title] of Object.entries(kinds)) {
      const row = element('fieldset'); row.append(element('legend', title));
      const choice = draft?.[kind] || {model:'',reasoning_effort:'',fast:false};
      const model = models.find(m => m.model === choice.model);
      const select = element('select'); select.id = 'codex-' + kind + '-model';
      select.append(...models.map(m => option(m.model, m.display_name + ' · ' + m.model)));
      if (!models.some(m=>m.model===choice.model)) select.append(option(choice.model,choice.model || 'Выберите модель'));
      select.value = choice.model; select.disabled = !draft;
      const effort = element('select'); effort.id = 'codex-' + kind + '-effort';
      const efforts = model?.reasoning_efforts || [];
      effort.append(...efforts.map(value=>option(value)));
      if (!efforts.includes(choice.reasoning_effort)) effort.append(option(choice.reasoning_effort,choice.reasoning_effort || 'Выберите усилие'));
      effort.value = choice.reasoning_effort; effort.disabled = !draft;
      const fast = element('input'); fast.type = 'checkbox'; fast.id = 'codex-' + kind + '-fast'; fast.checked = choice.fast; fast.disabled = !draft;
      for (const [text, control] of [['Модель ',select],['Усилие рассуждения ',effort],['Fast ',fast]]) {
        const label = element('label',text); label.append(control); row.append(label);
      }
      select.onchange = () => {choice.model = select.value; revision++; render();};
      effort.onchange = () => {choice.reasoning_effort = effort.value; revision++; validate();};
      fast.onchange = () => {choice.fast = fast.checked; revision++; validate();};
      byId('codex-choices').append(row);
    }
    validate();
  }
  async function catalog() {
    const action = operations.begin('catalog', {abortable:true});
    byId('codex-choice-status').textContent = 'Загрузка каталога…';
    try {
      const result = await api('processors/codex/models','GET',undefined,action.signal);
      action.commit(() => {
        models = result.models;
        if (loaded && !draft) {
          const initial = models.find(m=>m.default) || models[0];
          if (initial) draft = Object.fromEntries(Object.keys(kinds).map(kind=>[kind,{model:initial.model,reasoning_effort:initial.default_reasoning_effort,fast:false}]));
        }
        render();
      });
    } catch (error) {action.commit(() => {byId('codex-choice-status').textContent = error.message;});}
  }
  byId('codex-models-refresh').onclick = catalog;
  byId('codex-save').onclick = async () => {
    if (error() || saving) return;
    const action = operations.begin('save'), sentRevision = revision;
    const body = structuredClone(draft); saving = true; validate();
    try {
      await api('processors/codex','PUT',body);
      action.commit(() => {byId('codex-choice-status').textContent = sentRevision === revision ? 'Настройки сохранены для новой работы.' : 'Настройки сохранены. Новый ввод ещё не сохранён.';});
    } catch(error) {action.commit(() => {byId('codex-choice-status').textContent = error.message;});}
    finally {action.commit(() => {saving = false; byId('codex-save').disabled = !!error();});}
  };
  return {
    async open() {
      if (!loaded) {
        const action = operations.begin('load', {abortable:true}), before = revision;
        try {
          const result = await api('processors/codex','GET',undefined,action.signal);
          action.commit(() => {if (before === revision && !draft) draft = result.config; loaded = true; render();});
        } catch(error) {action.commit(() => {byId('codex-choice-status').textContent = error.message;});}
        if (action.current) await catalog();
      } else {render();}
    },
    deactivate() {operations.invalidate(); saving = false;},
  };
}
