import {element, labelled} from './controls.js';
import {reportError} from './debug.js';

export function createYouTubeHistory({container, feed, api, operations, values, onChanged, reload, message}) {
  const count = element('input');
  count.id = 'youtube-history-count'; count.type = 'number'; count.min = '1'; count.step = '1'; count.required = true;
  count.value = values.youtubeHistoryCount ?? feed.initial_history?.count_text ?? String(feed.initial_history?.count ?? 60);
  count.oninput = () => {values.youtubeHistoryCount = count.value;};
  const start = element('button', 'Загрузить последние N видео'); start.id = 'youtube-history-start';
  const stop = element('button', 'Остановить загрузку истории'); stop.id = 'youtube-history-stop';
  let busy = false;
  let current = feed.initial_history;
  function update(history) {
    current = history;
    const active = history && !['done','cancelled'].includes(history.state);
    start.disabled = busy || Boolean(active);
    stop.hidden = !active; stop.disabled = busy;
  }
  async function change(method) {
    if (busy) return;
    const sent = count.value;
    if (method === 'POST') {
      count.setCustomValidity(/^\d+$/.test(sent) ? '' : 'Введите целое количество от 1');
      if (!count.reportValidity()) return;
    }
    const action = operations.begin('history');
    busy = true; update(current);
    try {
      const number = Number(sent);
      const body = method === 'POST' ? {mode:'count',count:Number.isSafeInteger(number)?number:sent} : undefined;
      const history = await api('feeds/' + feed.id + '/history', method, body);
      if (method === 'POST' && values.youtubeHistoryCount === sent) delete values.youtubeHistoryCount;
      onChanged();
      await action.commit(reload);
      action.commit(() => message(method === 'POST' ? 'Загрузка истории начата' : 'Загрузка истории остановлена'));
      current = history;
    } catch (error) {
      reportError(error, 'Интерфейс Око');
      action.commit(() => message(error.message));
    } finally {busy = false; update(current);}
  }
  start.onclick = () => change('POST'); stop.onclick = () => change('DELETE');
  container.append(element('p', 'История: обычные видео и записи трансляций, без Shorts. Уже сохранённые видео не дублируются. Даты и описания старых видео могут отсутствовать.'),
    labelled('Последние N видео', count), start, stop);
  update(current);
  return {feedId:feed.id, update};
}
