import {createMaterialList} from './reading/list';
import {createReadingTree} from './reading/tree';
import {createMenus} from './settings/menus';
import {createDebugPanel, reportError} from './status/debug';
import {createOpenedMaterial} from './reading/opened';
import {createTransport} from './api/transport';
import {createStatusPanel} from './status/panel';
import {createReaderActions} from './status/actions';
import {createFolderActions} from './reading/folder-actions';
import {createReaderApi} from './api/reader';
import {createReaderSession} from './reader-session';
import {initializeReaderShell,mountSurface,setRenderReporter} from './renderer';

import {useState} from 'react';
import {ReaderShell} from './shell';
import {errorMessage} from './api/errors';
import {regenerate,retryMaterial,retryTheses,retryFilter} from './api/legacy-generated/sdk.gen';
import type {Navigation} from './api/legacy-generated/types.gen';
import type {ReaderMessage} from './reader-session';
initializeReaderShell(<ReaderShell/>);
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const transport = createTransport({fetch:window.fetch.bind(window), token, origin:location.origin});
const readerApi = createReaderApi({fetch:window.fetch.bind(window), token, origin:location.origin,onError:(error,source)=>reportError(error,source,true)});
const debug = createDebugPanel({token, readerApi});
setRenderReporter(reportError);
const byId = (id:string):HTMLElement => {const node=document.getElementById(id);if(!node)throw new Error("Missing reader element: "+id);return node;};
const input=(id:string)=>byId(id) as HTMLInputElement;
const button=(id:string)=>byId(id) as HTMLButtonElement;
const progress=(text:string)=>{mountSurface(byId("progress"),<>{text}</>);};
let contentEpoch = 0;
let stopped = false;
let commandsAvailable = false;
const commandHosts = new Map<string,HTMLElement>();
let folderId:string|null = null;
let feedId:string|null = null;
let refreshSequence = 0;
let readingCatalog:Navigation = {folders:[],feeds:[],count:0};
let statusPanel:ReturnType<typeof createStatusPanel>;
const actions = createReaderActions({readerApi,onChanged:()=>statusPanel?.redraw()});
readerApi.setActionTracker(actions);
const api={discardDraft:async(path:string,field:string,method?:string)=>{await statusPanel.discardDraft(path,field,method);void refresh();}};
const opened = createOpenedMaterial({
  readerApi,
  fetchMedia: (path, signal) => transport.media(path, signal, 'no-store'),
  onViewed: id => { contentEpoch++; debug.forgetMaterials([id]); statusPanel.forgetMaterials([id]); opened.viewed(id); list.removed(id); session.post(id); refresh(); },
  onChanged: changed => { if (changed) contentEpoch++; refresh(); },
});
const list = createMaterialList({readerApi,onOpen: card => opened.open(card), onRetry: () => refresh(), fetchMedia: transport.media});
const folderActions = createFolderActions({readerApi,getFolder:()=>folderId,isStopped:()=>stopped,onRefresh:refresh,onCompleted:folder=>{contentEpoch++;opened.folderCompleted(folder);if(folderId===folder)list.clear();session.post({kind:'folder-viewed',folder});}});
list.bindCommands(folderActions);
const menus = createMenus({api, readerApi, onChanged: () => {contentEpoch++; refresh();}});
statusPanel = createStatusPanel({debug, actions,onAvailability:available=>{commandsAvailable=available;renderCommands();}, onSettings:(target,trigger)=>menus.openStatus(target,trigger),
  onMaterial:id=>opened.open({id}), onRetry:async(target,problem)=>{
    if (target.kind === 'collect') await readerApi.collect();
    else {
      const ids=problem.objects.filter(object=>object.kind==='material').map(object=>object.id);
      for(const id of ids.length?ids:target.id?[target.id]:[]) {
        const options={client:readerApi.client,path:{item:id}};
        if(target.kind==='retry')await retryMaterial(options);
        else if(target.kind==='theses-retry')await retryTheses(options);
        else if(target.kind==='filter-retry')await retryFilter(options);
        else if(target.kind==='regenerate')await regenerate(options);
      }
    }
    refresh();
  }, onRefresh:()=>refresh()});
const tree = createReadingTree({readerApi,onSelect: selection => {folderId = selection.kind === 'folder' ? selection.id : null; feedId = selection.kind === 'feed' ? selection.id : null; contentEpoch++; opened.clear(); list.setArea(selection, readingCatalog); refresh();}, onFeedSettings: (id, trigger) => menus.openFeed(id, trigger), onWarning: () => statusPanel.open()});
function stopUi(message:string) {
  session.dispose();
  menus.stop();
  stopped = true;
  statusPanel.stop();
  contentEpoch++;
  opened.clear();
  list.clear();
  tree.clear();
  progress(message);
  button('stop').disabled = true;
  button('collect').disabled = true;
  button('folder-viewed').disabled = true;
  folderActions.stop();

  void debug.refresh();
}
function readerMessage(data:ReaderMessage) {
  if (data === 'stop') stopUi('Око остановлено');
  else if (typeof data==='object') {
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
    const state = await readerApi.status();
    if (stopped || epoch !== contentEpoch || sequence !== refreshSequence) return;
    statusAvailable = true;
    debug.updateStatus(state.debug_count, state.debug_persistence_error, debugRevision);
    menus.status(state);
    if (state.stopped) { stopUi('Око остановлено'); return; }
    statusPanel.update(state.problems, state);
    debug.forgetMaterials(state.viewed_ids || []);
    statusPanel.forgetMaterials(state.viewed_ids || []);
    void actions.flush();
    progress(`${state.stage} · источники ${state.ready_sources}/${state.total_sources}`);
    menus.sources(state.sources || []);
    const navigation = await tree.load();
    if (stopped || epoch !== contentEpoch || sequence !== refreshSequence) return;
    readingCatalog = navigation;
    if (tree.update(navigation, state.sources || [])) return;
    list.setArea(tree.selection, navigation);
    if (state.error) { opened.clear(); list.error(state.error); return; }
    const params = new URLSearchParams({sort: input('sort').value});
    if (input('show-hidden').checked) params.set('show_hidden', 'true');
    if (folderId) params.set('folder', folderId);
    if (feedId) params.set('feed', feedId);
    const cards = await list.load(params);
    if (stopped || epoch !== contentEpoch || sequence !== refreshSequence) return;
    opened.reconcile(cards);
    list.update(cards, opened.selection?.id);

  } catch (error) { reportError(error, 'Интерфейс Око'); if (!stopped && epoch === contentEpoch && sequence === refreshSequence) { if (!statusAvailable) {opened.clear();statusPanel.unavailable();} list.error(errorMessage(error)); progress(statusAvailable?'Чтение временно недоступно':'Нет связи с Око'); } }
}

function Command({id,label,run,disabled}:{id:string;label:string;run:()=>Promise<unknown>;disabled:boolean}) {
  const [message,setMessage]=useState('');
  return <><button id={id} type="button" disabled={disabled} onClick={async()=>{setMessage('');try{await run();}catch(error){reportError(error,'Интерфейс Око');setMessage(errorMessage(error));}}}><span className="reader-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{id==='collect'?<path d="M20 7v5h-5M4 17v-5h5M19 12a7 7 0 0 0-12-5L4 10m16 4-3 3a7 7 0 0 1-12-5"/>:<rect x="6" y="6" width="12" height="12" rx="2"/>}</svg></span>{label}</button><span className="header-command-error" role="status" hidden={!message}>{message}</span></>;
}
function renderCommands(){for(const [id,label,run] of [
  ['collect','Обновить источники',async()=>{await readerApi.collect();await refresh();}],
  ['stop','Остановить Око',async()=>{await readerApi.stop();session.post('stop');stopUi('Око остановлено');}],
] as const) {
  let host=commandHosts.get(id);
  if(!host){const control=byId(id);host=document.createElement('span');host.className='header-command';control.replaceWith(host);commandHosts.set(id,host);}
  mountSurface(host,<Command id={id} label={label} run={run} disabled={!commandsAvailable}/>);
}}
renderCommands();
document.querySelector('.reader-toolbar-scroll')!.addEventListener('focusin',event=>{const control=event.target instanceof Element?event.target.closest('button'):null;control?.scrollIntoView({block:'nearest',inline:'nearest'});});
byId('sort').onchange = () => { contentEpoch++; refresh(); };
const session = createReaderSession({api:readerApi,onRefresh:updateReader,onMessage:readerMessage,
  onUnavailable:error=>{reportError(error,'Интерфейс Око');if(!stopped){opened.clear();statusPanel.unavailable();list.error(errorMessage(error));progress('Нет связи с Око');}},
  onClosed:()=>{contentEpoch++;opened.clear();list.clear();},onCloseError:error=>reportError(error,'Закрытие вкладки')});
session.start();

byId('show-hidden').onchange = () => { contentEpoch++; opened.clear(); refresh(); };
