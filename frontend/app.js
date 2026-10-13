import {createMaterialList} from './material-list.js';
import {createReadingTree} from './reading-tree.js';
import {createMenus} from './menus.js';
import {createDebugPanel, reportError} from './debug.js';
import {createOpenedMaterial} from './opened-material.js';
import {createTransport} from './transport.js';

'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const transport = createTransport({fetch:window.fetch.bind(window), token, origin:location.origin});
const debug = createDebugPanel({token, transport});
const tab = crypto.randomUUID();
const byId = id => document.getElementById(id);
const bus = new BroadcastChannel('oko-reader');
let contentEpoch = 0;
let stopped = false;
let folderId = null;
let feedId = null;
let refreshTimer = null;
let refreshSequence = 0;
let readingCatalog = {folders:[], feeds:[]};
async function api(path, method = 'GET', body = undefined, signal = undefined) {
  try {
    return await transport.json('/api/' + path, method, body, signal);
  } catch (error) { reportError(error, method + ' /api/' + path, true); throw error; }
}

const opened = createOpenedMaterial({
  api,
  fetchMedia: (path, signal) => transport.media(path, signal, 'no-store'),
  onViewed: id => { contentEpoch++; opened.viewed(id); list.removed(id); bus.postMessage(id); refresh(); },
  onChanged: changed => { if (changed) contentEpoch++; refresh(); },
});
const list = createMaterialList({onOpen: card => opened.open(card), onRetry: () => refresh(), fetchMedia: transport.media});
const menus = createMenus({api, onChanged: () => {contentEpoch++; refresh();}});
const tree = createReadingTree({onSelect: selection => {folderId = selection.kind === 'folder' ? selection.id : null; feedId = selection.kind === 'feed' ? selection.id : null; contentEpoch++; opened.clear(); list.setArea(selection, readingCatalog); refresh();}, onFeedSettings: (id, trigger) => menus.openFeed(id, trigger), onWarning: () => {byId('debug-toggle').checked = true; byId('debug-toggle').dispatchEvent(new Event('change'));}});
function stopUi(message) {
  menus.stop();
  stopped = true;
  contentEpoch++;
  opened.clear();
  list.clear();
  byId('folders').replaceChildren();
  byId('count').textContent = '0';
  byId('progress').textContent = message;
  byId('review-restarts').replaceChildren();
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
    opened.viewed(event.data); list.removed(event.data); refresh();
  }
};
async function refresh() {
  clearTimeout(refreshTimer);
  if (stopped) return;
  const epoch = contentEpoch;
  const sequence = ++refreshSequence;
  const debugRevision = debug.revision();
  let statusAvailable = false;
  try {
    const state = await api('status');
    if (stopped || epoch !== contentEpoch || sequence !== refreshSequence) return;
    statusAvailable = true;
    debug.updateStatus(state.debug_count, state.debug_persistence_error, debugRevision);
    menus.status(state);
    if (state.stopped) { stopUi('Око остановлено'); return; }
    const readMessages = (state.telegram_reads || []).filter(r => r.pending || r.waiting || r.error).map(r => r.error || r.waiting || `Telegram: ожидают подтверждения ${r.pending}`);
    const progressMessages = [...new Set([...state.errors, ...readMessages])];
    byId('progress').textContent = state.error || `${state.stage} · источники ${state.ready_sources}/${state.total_sources}` + (progressMessages.length ? ' · ' + progressMessages.join(', ') : '');
    byId('review-restarts').replaceChildren(...(state.review_restarts || []).map(review => {
      const row = element('p', review.error + ' ');
      const button = element('button', 'Пересоздать обзор');
      button.onclick = async () => {
        button.disabled = true;
        try {await api('materials/' + review.id + '/regenerate', 'POST'); refresh();}
        catch(error) {if (!stopped) byId('progress').textContent = error.message;}
        finally {button.disabled = stopped;}
      };
      row.append(button); return row;
    }));
    menus.sources(state.sources || []);
    const navigation = await api('navigation');
    if (stopped || epoch !== contentEpoch || sequence !== refreshSequence) return;
    readingCatalog = navigation;
    if (tree.update(navigation, state.sources || [])) return;
    list.setArea(tree.selection, navigation);
    if (state.error) { opened.clear(); list.error(state.error); return; }
    const params = new URLSearchParams({sort: byId('sort').value});
    if (byId('show-hidden').checked) params.set('show_hidden', 'true');
    if (folderId) params.set('folder', folderId);
    if (feedId) params.set('feed', feedId);
    const cards = await api('cards?' + params);
    if (stopped || epoch !== contentEpoch || sequence !== refreshSequence) return;
    opened.reconcile(cards);
    list.update(cards, opened.selection?.id);

  } catch (error) { reportError(error, 'Интерфейс Око'); if (!stopped && epoch === contentEpoch && sequence === refreshSequence) { if (!statusAvailable) opened.clear(); list.error(error.message); byId('progress').textContent = error.message; } }
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
  catch (error) { reportError(error, 'Интерфейс Око'); if (!stopped) { opened.clear(); list.error(error.message); byId('progress').textContent = error.message; } }
  if (!stopped) setTimeout(heartbeat, 2000);
}
window.addEventListener('pagehide', () => {
  transport.close('/api/tabs/' + tab + '/close').catch(error => reportError(error, 'Закрытие вкладки'));
  opened.clear(); list.clear();
});
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });

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
    if (folderId === confirmedFolder) list.clear();
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
