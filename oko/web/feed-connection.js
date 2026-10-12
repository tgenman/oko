import {reportError} from './debug.js';
import {bindInitialHistory} from './initial-history.js';

export function createFeedConnection({api, onSelected}) {
  const byId = id => document.getElementById(id);
  const historySelection = bindInitialHistory('rss');
  const url = byId('rss-url'), check = byId('rss-check'), add = byId('rss-add');
  let generation = 0, checked = null, pending = false, saving = false, stopped = false, controller = null;
  function controls() {
    url.disabled = stopped || saving;
    check.disabled = stopped || pending || !url.value.trim();
    add.disabled = stopped || pending || !checked;
  }
  function reset() {
    generation++; controller?.abort(); checked = null; pending = false;
    byId('rss-checked-fields').hidden = true;
    byId('rss-title').disabled = byId('rss-folder').disabled = true;
    byId('rss-message').textContent = '';
    controls();
  }
  url.addEventListener('input', reset);
  check.onclick = async () => {
    if (check.disabled || !url.reportValidity()) return;
    const stamp = ++generation;
    checked = null; pending = true; controller = new AbortController(); controls();
    byId('rss-checked-fields').hidden = true;
    byId('rss-title').disabled = byId('rss-folder').disabled = true;
    byId('rss-message').textContent = 'Проверка фида…';
    try {
      const result = await api('feeds/verify', 'POST', {url: url.value.trim()}, controller.signal);
      if (stamp !== generation || stopped) return;
      if (result.existing) {
        await onSelected(result.existing);
        if (stamp !== generation || stopped) return;
        byId('rss-message').textContent = 'Уже добавлен: ' + result.existing.title;
      } else {
        checked = result;
        byId('rss-title').value = result.title;
        byId('rss-title').disabled = byId('rss-folder').disabled = false;
        byId('rss-checked-fields').hidden = false;
        byId('rss-message').textContent = result.kind === 'youtube' ? 'YouTube-фид проверен' : 'RSS/Atom-фид проверен';
      }
    } catch (error) {
      reportError(error, 'Проверка фида');
      if (stamp === generation && !stopped && error.name !== 'AbortError') byId('rss-message').textContent = error.message;
    } finally { if (stamp === generation) { pending = false; controls(); } }
  };
  byId('rss-form').onsubmit = async event => {
    event.preventDefault();
    if (add.disabled || !checked) return;
    const initialHistory = historySelection();
    if (!initialHistory) return;
    const stamp = generation, preview = checked;
    pending = true; saving = true; controls(); byId('rss-message').textContent = 'Добавление…';
    try {
      const result = await api('feeds', 'POST', {url: preview.url, verification: preview.verification,
        title: byId('rss-title').value.trim(), folder: byId('rss-folder').value.trim(), initial_history: initialHistory});
      if (stamp !== generation || stopped) return;
      await onSelected(result);
      if (stamp !== generation || stopped) return;
      byId('rss-message').textContent = result.title + ' · ' + (result.kind === 'youtube' ? 'YouTube подключён автоматически' : 'RSS подключён');
      checked = null;
    } catch (error) {
      reportError(error, 'Добавление фида');
      if (stamp === generation && !stopped) byId('rss-message').textContent = error.message;
    } finally { if (stamp === generation) { pending = false; saving = false; controls(); } }
  };
  controls();
  return {stop() { stopped = true; reset(); }};
}
