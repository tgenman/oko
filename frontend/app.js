import {createMaterialList} from './src/reading/list.tsx';
import {createReadingTree} from './src/reading/tree.tsx';
import {createMenus} from './menus.js';
import {createDebugPanel, reportError} from './debug.js';
import {createOpenedMaterial} from './src/reading/opened.tsx';
import {createTransport} from './transport.js';
import {createStatusPanel} from './status-panel.js';
import {createReaderActions} from './reader-actions.js';
import {createFolderActions} from './src/reading/folder-actions.tsx';
import {createReaderApi} from './src/api/reader.ts';
import {createReaderSession} from './src/reader-session.ts';
import {setRenderReporter} from './src/renderer.tsx';

'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const transport = createTransport({fetch:window.fetch.bind(window), token, origin:location.origin});
const readerApi = createReaderApi({fetch:window.fetch.bind(window), token, origin:location.origin,onError:(error,source)=>reportError(error,source,true)});
const debug = createDebugPanel({token, transport});
setRenderReporter(reportError);
const byId = id => document.getElementById(id);
let contentEpoch = 0;
let stopped = false;
let folderId = null;
let feedId = null;
let refreshSequence = 0;
let readingCatalog = {folders:[], feeds:[]};
let statusPanel;
const actions = createReaderActions({transport,onChanged:()=>statusPanel?.redraw()});
readerApi.setActionTracker(actions);
async function api(path, method = 'GET', body = undefined, signal = undefined) {
  const request = actions.begin(path, method, body);
  try {
    const result = path === 'status' && method === 'GET' ? await readerApi.status(signal) : path === 'collect' && method === 'POST' ? await readerApi.collect(request?.id) : path === 'stop' && method === 'POST' ? await readerApi.stop(request?.id) : await transport.json('/api/' + path, method, body, signal, request?.id);
    actions.succeeded(request); return result;
  } catch (error) {error.materialIds = path.startsWith('materials/') ? [path.split('/')[1]] : []; actions.failed(request,error); if(!readerApi.wasReported(error))reportError(error, method + ' /api/' + path, true); throw error; }
}
api.discardDraft = async(path,field,method)=>{await statusPanel.discardDraft(path,field,method);refresh();};

const opened = createOpenedMaterial({
  readerApi,
  fetchMedia: (path, signal) => transport.media(path, signal, 'no-store'),
  onViewed: id => { contentEpoch++; debug.forgetMaterials([id]); statusPanel.forgetMaterials([id]); opened.viewed(id); list.removed(id); session.post(id); refresh(); },
  onChanged: changed => { if (changed) contentEpoch++; refresh(); },
});
const list = createMaterialList({readerApi,onOpen: card => opened.open(card), onRetry: () => refresh(), fetchMedia: transport.media});
const folderActions = createFolderActions({readerApi,getFolder:()=>folderId,isStopped:()=>stopped,onRefresh:refresh,onCompleted:folder=>{contentEpoch++;opened.folderCompleted(folder);if(folderId===folder)list.clear();session.post({kind:'folder-viewed',folder});}});
list.bindCommands(folderActions);
const menus = createMenus({api, onChanged: () => {contentEpoch++; refresh();}});
statusPanel = createStatusPanel({debug, actions, onSettings:(target,trigger)=>menus.openStatus(target,trigger),
  onMaterial:id=>opened.open({id}), onRetry:async(target,problem)=>{
    const suffix = {retry:'retry','theses-retry':'theses/retry','filter-retry':'filter/retry',regenerate:'regenerate'}[target.kind];
    if (target.kind === 'collect') await api('collect', 'POST');
    else if (suffix) {
      const ids = problem.objects.filter(object=>object.kind==='material').map(object=>object.id);
      for (const id of ids.length ? ids : [target.id]) await api('materials/' + id + '/' + suffix, 'POST');
    }
    refresh();
  }, onRefresh:()=>refresh()});
const tree = createReadingTree({readerApi,onSelect: selection => {folderId = selection.kind === 'folder' ? selection.id : null; feedId = selection.kind === 'feed' ? selection.id : null; contentEpoch++; opened.clear(); list.setArea(selection, readingCatalog); refresh();}, onFeedSettings: (id, trigger) => menus.openFeed(id, trigger), onWarning: () => statusPanel.open()});
function stopUi(message) {
  session.dispose();
  menus.stop();
  stopped = true;
  statusPanel.stop();
  contentEpoch++;
  opened.clear();
  list.clear();
  tree.clear();
  byId('count').textContent = '0';
  byId('progress').textContent = message;
  byId('review-restarts').replaceChildren();
  byId('stop').disabled = true;
  byId('collect').disabled = true;
  byId('compress').disabled = true;
  byId('folder-viewed').disabled = true;
  folderActions.stop();
  byId('digest-now').disabled = true;

  void debug.refresh();
}
function readerMessage(data) {
  if (data === 'stop') stopUi('Око остановлено');
  else if (data?.kind === 'folder-viewed') {
    contentEpoch++;
    opened.folderCompleted(data.folder);
    refresh();
  } else {
    contentEpoch++;
    debug.forgetMaterials([data]); statusPanel.forgetMaterials([data]); opened.viewed(data); list.removed(data); refresh();
  }
}
function refresh() {return session.refresh();}
async function updateReader() {
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
    statusPanel.update(state.problems, state);
    debug.forgetMaterials(state.viewed_ids || []);
    statusPanel.forgetMaterials(state.viewed_ids || []);
    void actions.flush();
    byId('progress').textContent = `${state.stage} · источники ${state.ready_sources}/${state.total_sources}`;
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
    const navigation = await tree.load();
    if (stopped || epoch !== contentEpoch || sequence !== refreshSequence) return;
    readingCatalog = navigation;
    if (tree.update(navigation, state.sources || [])) return;
    list.setArea(tree.selection, navigation);
    if (state.error) { opened.clear(); list.error(state.error); return; }
    const params = new URLSearchParams({sort: byId('sort').value});
    if (byId('show-hidden').checked) params.set('show_hidden', 'true');
    if (folderId) params.set('folder', folderId);
    if (feedId) params.set('feed', feedId);
    const cards = await list.load(params);
    if (stopped || epoch !== contentEpoch || sequence !== refreshSequence) return;
    opened.reconcile(cards);
    list.update(cards, opened.selection?.id);

  } catch (error) { reportError(error, 'Интерфейс Око'); if (!stopped && epoch === contentEpoch && sequence === refreshSequence) { if (!statusAvailable) {opened.clear();statusPanel.unavailable();} list.error(error.message); byId('progress').textContent = statusAvailable ? 'Чтение временно недоступно' : 'Нет связи с Око'; } }
}
function actionFeedback(id) {
  const button = byId(id), wrapper = document.createElement('span'), message = document.createElement('span');
  wrapper.className = 'header-command';message.className = 'header-command-error';message.setAttribute('role','status');message.hidden=true;
  button.before(wrapper);wrapper.append(button,message);return text=>{message.textContent=text;message.hidden=!text;};
}
const collectFeedback = actionFeedback('collect'), stopFeedback = actionFeedback('stop');
document.querySelector('.reader-toolbar-scroll').addEventListener('focusin', event => {
  const button = event.target.closest('button');
  if (button) button.scrollIntoView({block:'nearest',inline:'nearest'});
});
byId('collect').onclick = async () => { collectFeedback('');try { await api('collect', 'POST'); refresh(); } catch (error) { reportError(error, 'Интерфейс Око'); collectFeedback(error.message); } };
byId('stop').onclick = async () => {
  stopFeedback('');
  try { await api('stop', 'POST'); session.post('stop'); stopUi('Око остановлено'); }
  catch (error) { reportError(error, 'Интерфейс Око'); stopFeedback(error.message); }
};
byId('sort').onchange = () => { contentEpoch++; refresh(); };
const session = createReaderSession({api:readerApi,onRefresh:updateReader,onMessage:readerMessage,
  onUnavailable:error=>{reportError(error,'Интерфейс Око');if(!stopped){opened.clear();statusPanel.unavailable();list.error(error.message);byId('progress').textContent='Нет связи с Око';}},
  onClosed:()=>{opened.clear();list.clear();},onCloseError:error=>reportError(error,'Закрытие вкладки')});
session.start();

function element(tag, text = '') { const node = document.createElement(tag); node.textContent = text; return node; }
byId('show-hidden').onchange = () => { contentEpoch++; opened.clear(); refresh(); };
