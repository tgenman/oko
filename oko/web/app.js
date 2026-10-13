import {createMenus} from './menus.js';
import {createDebugPanel, reportError} from './debug.js';
import {createOpenedMaterial} from './opened-material.js';

'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const debug = createDebugPanel({token});
const tab = crypto.randomUUID();
const byId = id => document.getElementById(id);
const bus = new BroadcastChannel('oko-reader');
let contentEpoch = 0;
let stopped = false;
let folderId = null;
let refreshTimer = null;
async function api(path, method = 'GET', body = undefined, signal = undefined) {
  try {
    const response = await fetch('/api/' + path, {method, signal, cache: 'no-store', headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(typeof result.detail === 'string' ? result.detail : response.status === 422 ? 'Проверьте значения настроек' : 'Связь с Око потеряна');
      error.detail = result.detail;
    error.diagnosticId = response.headers.get('X-Oko-Error');
    if (response.headers.get('X-Oko-Expected')) error.name = 'AbortError';
      throw error;
    }
    return result;
  } catch (error) { reportError(error, method + ' /api/' + path, true); throw error; }
}

const opened = createOpenedMaterial({
  api,
  fetchMedia: (path, signal) => fetch(path, {signal, cache: 'no-store', headers: {'Authorization': 'Bearer ' + token}}),
  onViewed: id => { contentEpoch++; opened.viewed(id); bus.postMessage(id); refresh(); },
  onChanged: changed => { if (changed) contentEpoch++; refresh(); },
});
const menus = createMenus({api, onChanged: () => {contentEpoch++; refresh();}});
function stopUi(message) {
  menus.stop();
  stopped = true;
  contentEpoch++;
  opened.clear();
  byId('cards').replaceChildren();
  byId('folders').replaceChildren();
  byId('count').textContent = '0';
  byId('progress').textContent = message;
  byId('stop').disabled = true;
  byId('collect').disabled = true;
  byId('compress').disabled = true;
  byId('folder-viewed').disabled = true;
  byId('completion-dialog').close();
  byId('digest-now').disabled = true;
  byId('digest-dialog').close();
  void debug.refresh();
}
bus.onmessage = event => {
  if (event.data === 'stop') stopUi('Око остановлено');
  else if (event.data?.kind === 'folder-viewed') {
    contentEpoch++;
    opened.folderCompleted(event.data.folder);
    refresh();
  } else {
    contentEpoch++;
    opened.viewed(event.data);
  }
};
async function refresh() {
  clearTimeout(refreshTimer);
  if (stopped) return;
  const epoch = contentEpoch;
  try {
    const state = await api('status');
    if (stopped || epoch !== contentEpoch) return;
    debug.updateStatus(state.debug_count, state.debug_persistence_error);
    menus.status(state.initial_histories || []);
    if (state.stopped) { stopUi('Око остановлено'); return; }
    const readMessages = (state.telegram_reads || []).filter(r => r.pending || r.waiting || r.error).map(r => r.error || r.waiting || `Telegram: ожидают подтверждения ${r.pending}`);
    const progressMessages = [...new Set([...state.errors, ...readMessages])];
    byId('progress').textContent = state.error || `${state.stage} · источники ${state.ready_sources}/${state.total_sources}` + (progressMessages.length ? ' · ' + progressMessages.join(', ') : '');
    const changesOpen = !!byId('source-status').querySelector('details[open]');
    byId('source-status').replaceChildren(...(state.sources || []).filter(source => source.error || source.gap).map(source => element('p', source.title + ': ' + [source.error, source.gap].filter(Boolean).join(' · '))));
    const changes = (state.sources || []).filter(source => source.changes);
    if (changes.length) {
      const details = element('details'); details.open = changesOpen; details.append(element('summary', 'Возможности проверки изменений'));
      for (const source of changes) details.append(element('p', source.title + ': ' + source.changes));
      byId('source-status').append(details);
    }
    if (state.error) { opened.clear(); return; }
    const params = new URLSearchParams({sort: byId('sort').value});
    if (byId('show-hidden').checked) params.set('show_hidden', 'true');
    if (folderId) params.set('folder', folderId);
    const cards = await api('cards?' + params);
    if (stopped || epoch !== contentEpoch) return;
    opened.reconcile(cards);
    byId('compress').disabled = !folderId;
    byId('folder-viewed').disabled = !folderId;
    byId('digest-now').disabled = !folderId;
    byId('count').textContent = cards.filter(card => !card.filter?.hidden).length;
    byId('cards').replaceChildren(...cards.map(card => {
      const button = document.createElement('button');
      button.classList.toggle('active', opened.selection?.id === card.id);
      button.textContent = (card.digest ? 'Обзор · ' : card.event ? 'Событие · ' : card.filter?.hidden ? 'Скрыто · ' : '') + card.title + (card.changed ? ' · Изменено' : '');
      const date = document.createElement('small');
      date.textContent = card.published_known === false ? 'Дата неизвестна' : new Date(card.published * 1000).toLocaleString('ru');
      button.append(date);
      button.onclick = () => opened.open(card);
      return button;
    }));
    const folders = await api('folders');
    if (stopped || epoch !== contentEpoch) return;
    byId('folders').replaceChildren(...folders.map(folder => {
      const button = document.createElement('button');
      button.textContent = `${folder.title} · ${folder.count}`;
      button.classList.toggle('active', folder.id === folderId);
      button.onclick = () => { folderId = folder.id; contentEpoch++; opened.clear(); refresh(); };
      return button;
    }));
  } catch (error) { reportError(error, 'Интерфейс Око'); if (!stopped && epoch === contentEpoch) { opened.clear(); byId('progress').textContent = error.message; } }
  finally { if (!stopped) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 500); } }
}
byId('collect').onclick = async () => { try { await api('collect', 'POST'); refresh(); } catch (error) { reportError(error, 'Интерфейс Око'); byId('progress').textContent = error.message; } };
byId('stop').onclick = async () => {
  try { await api('stop', 'POST'); bus.postMessage('stop'); stopUi('Око остановлено'); }
  catch (error) { reportError(error, 'Интерфейс Око'); byId('progress').textContent = error.message; }
};
async function heartbeat() {
  if (stopped) return;
  try { await api('tabs/' + tab + '/heartbeat', 'POST'); }
  catch (error) { reportError(error, 'Интерфейс Око'); if (!stopped) { opened.clear(); byId('progress').textContent = error.message; } }
  if (!stopped) setTimeout(heartbeat, 2000);
}
window.addEventListener('pagehide', () => {
  fetch('/api/tabs/' + tab + '/close', {method:'POST', keepalive:true, headers:{'Authorization':'Bearer '+token}}).catch(error => reportError(error, 'Закрытие вкладки'));
  opened.clear();
});
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
byId('all-folders').onclick = () => { folderId = null; contentEpoch++; opened.clear(); refresh(); };
byId('sort').onchange = () => { contentEpoch++; refresh(); };
heartbeat().then(refresh);

function element(tag, text = '') { const node = document.createElement(tag); node.textContent = text; return node; }
byId('show-hidden').onchange = () => { contentEpoch++; opened.clear(); refresh(); };
let digestPeriod = false;
function openDigest(period) {
  if (!folderId || stopped) return;
  digestPeriod = period;
  byId('digest-heading').textContent = period ? 'Обновить обзор за период' : 'Сжать историю папки';
  byId('digest-start').value = ''; byId('digest-end').value = '';
  byId('digest-message').textContent = period ? 'Без дат используется настроенный период обзора.' : '';
  byId('digest-dialog').showModal();
}
byId('compress').onclick = () => openDigest(false);
byId('digest-now').onclick = () => openDigest(true);
byId('digest-close').onclick = () => byId('digest-dialog').close();
byId('digest-run').onclick = async () => {
  const button = byId('digest-run'); button.disabled = true;
  try {
    const body = {period: digestPeriod};
    for (const field of ['start', 'end']) {
      const value = byId('digest-' + field).value;
      if (value) body[field] = new Date(value).getTime() / 1000;
    }
    await api('digests/folder/' + folderId, 'POST', body);
    byId('digest-dialog').close(); refresh();
  } catch (error) { reportError(error, 'Интерфейс Око'); byId('digest-message').textContent = error.message; }
  finally { button.disabled = stopped; }
};

let completionFolder = null;
let completionToken = null;
let completionGeneration = 0;
function renderCompletion(preview) {
  completionToken = preview.token;
  byId('completion-count').textContent = `Карточек: ${preview.cards}. Исходных публикаций: ${preview.originals}.`;
  byId('completion-confirm').disabled = stopped || preview.originals === 0;
}
byId('folder-viewed').onclick = async () => {
  if (!folderId || stopped) return;
  const stamp = ++completionGeneration;
  completionFolder = folderId;
  completionToken = null;
  byId('completion-count').textContent = 'Подсчёт…';
  byId('completion-message').textContent = '';
  byId('completion-confirm').disabled = true;
  byId('completion-dialog').showModal();
  try {
    const preview = await api('folders/' + completionFolder + '/completion/preview', 'POST');
    if (stamp === completionGeneration && !stopped && byId('completion-dialog').open) renderCompletion(preview);
  } catch (error) { reportError(error, 'Интерфейс Око'); if (stamp === completionGeneration) byId('completion-message').textContent = error.message; }
};
byId('completion-dialog').addEventListener('close', () => { completionGeneration++; completionToken = null; });
byId('completion-close').onclick = () => byId('completion-dialog').close();
byId('completion-confirm').onclick = async () => {
  if (!completionToken || stopped) return;
  const stamp = completionGeneration;
  const confirmedFolder = completionFolder;
  byId('completion-confirm').disabled = true;
  try {
    await api('folders/' + confirmedFolder + '/completion', 'POST', {token: completionToken});
    contentEpoch++;
    opened.folderCompleted(confirmedFolder);
    byId('cards').replaceChildren();
    bus.postMessage({kind: 'folder-viewed', folder: confirmedFolder});
    byId('completion-dialog').close();
    refresh();
  } catch (error) { reportError(error, 'Интерфейс Око');
    if (!error.detail?.preview) {
      contentEpoch++;
      opened.folderCompleted(confirmedFolder);
      bus.postMessage({kind: 'folder-viewed', folder: confirmedFolder});
    }
    refresh();
    if (stamp !== completionGeneration || stopped) return;
    byId('completion-message').textContent = error.detail?.message || error.message;
    if (error.detail?.preview) renderCompletion(error.detail.preview);
    else byId('completion-confirm').disabled = false;
  }
};
