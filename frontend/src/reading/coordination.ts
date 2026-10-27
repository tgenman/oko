import {createReadingResources,listIdentity} from './resources';
import type {ReadingListOptions} from '../api/generated/types.gen';
import {createNewsActions} from './news-actions';
import {createMaterialList} from './list';
import {createReadingTree, type Navigation} from './tree';
import {createOpenedMaterial} from './opened';
import {createFeedActions} from './feed-actions';
import {createFolderMenu} from './folder-menu';
import {createFolderActions} from './folder-actions';
import {createReaderSession, type ReaderMessage} from '../reader-session';
import {reportError, type createDebugPanel} from '../status/debug';
import {errorMessage} from '../api/errors';
import type {ReaderApi} from '../api/reader';
import type {AffectedResource} from '../api/generated/types.gen';
import type {createMenus} from '../settings/menus';
import type {createStatusPanel} from '../status/panel';
import type {createReaderActions} from '../status/actions';
import type {createTransport} from '../api/transport';

type Options = {
  readerApi: ReaderApi;
  fetchMedia: ReturnType<typeof createTransport>['media'];
  debug: ReturnType<typeof createDebugPanel>;
  menus: ReturnType<typeof createMenus>;
  statusPanel: ReturnType<typeof createStatusPanel>;
  actions: ReturnType<typeof createReaderActions>;
  progress: (text: string) => void;
  onCollectionPoll: () => void;
  onStopped: () => void;
  onPowerState: (enabled:boolean,available:boolean) => void;
};

// Coordinates accepted reading changes. Local owners retain DOM, scroll, drafts
// and media; the session alone owns polling, presence and browser resources.
export function createReadingCoordination({readerApi, fetchMedia, debug, menus, statusPanel, actions, progress, onCollectionPoll, onStopped, onPowerState}: Options) {
  const resources=createReadingResources(readerApi);
  let checking:AbortController|undefined;
  let unavailable=false;
  let displayedList:string|undefined;
  let listOnly=false;
  let settingsAppliedRevision:number|undefined;
  let contentEpoch = 0;
  let refreshSequence = 0;
  let stopped = false;
  let workAllowed = false;
  const refreshing = new Set<string>();
  async function refreshTarget(target:import('../api/generated/types.gen').CollectionTarget){
    const key=JSON.stringify(target);if(!workAllowed||refreshing.has(key))return;
    refreshing.add(key);
    try{await readerApi.collect(target);await refresh();}
    catch(error){reportError(error,'Refresh');progress(errorMessage(error));}
    finally{refreshing.delete(key);}
  }
  let readingCatalog: Navigation = {folders: [], feeds: [], count: 0};
  const input = (id: string) => document.getElementById(id) as HTMLInputElement;

  function viewed(id: string, broadcast: boolean) {
    contentEpoch++;
    resources.forget([id]);
    debug.forgetMaterials([id]);
    statusPanel.forgetMaterials([id]);
    opened.viewed(id);
    list.removed(id);
    if (broadcast) session.post(id);
  }
  function changed() {
    contentEpoch++;
    return refresh();
  }
  const opened = createOpenedMaterial({
    readerApi,
    getFeed:id=>readingCatalog.feeds.find(feed=>feed.id===id),
    acceptStorage:identity=>resources.storageId===null||resources.storageId===identity,
    fetchMedia: (path, signal) => fetchMedia(path, signal, 'no-store'),
    onViewed: id => { viewed(id, true); void refresh(); },
    onChanged: contentChanged => {if (contentChanged) contentEpoch++; void refresh();},
  });
  const list = createMaterialList({
    readerApi, fetchMedia,
    onContext: (card, context, x, y, trigger) => {folderMenu.close(false);feedActions.close(false);newsActions.open(card, context, x, y, trigger);},
    onOpen: card => opened.open(card),
    onRetry: refresh,
  });
  const newsActions = createNewsActions({
    readerApi, isStopped: () => stopped,
    onViewed: id => {if(opened.selection?.id===id)opened.clear();viewed(id, true);},
    onRefresh: changed,
    onPreview: ids => list.preview(ids),
  });
  const folderActions = createFolderActions({
    readerApi, isStopped: () => stopped,
    getFolder: () => tree.selection.kind === 'folder' ? tree.selection.id : null,
    onSettled: folder => {
      contentEpoch++;
      // A captured token does not cover later arrivals; refresh reconciles exact cards.
      session.post({kind: 'folder-viewed', folder});
      void refresh();
    },
  });
  list.bindCommands(folderActions);
  const feedActions = createFeedActions({
    readerApi, isStopped: () => stopped,canRefresh:()=>workAllowed,
    onRefresh:id=>refreshTarget({scope:'feed',feed_id:id}),
    onSettings: (id, trigger) => {void menus.openFeed(id, trigger);},
    onChanged: changed,
    onSettled: feed => {
      // A completion token cannot cover later arrivals. Reconcile the accepted
      // cards on refresh instead of clearing everything belonging to this feed.
      contentEpoch++;
      session.post({kind: 'feed-viewed', feed});
      void refresh();
    },
    onMove: id => menus.feedMoved(id),
    removeFeed: id => menus.removeFeed(id),
  });
  const folderMenu = createFolderMenu({
    readerApi, isStopped: () => stopped,canRefresh:()=>workAllowed,
    onRefresh:id=>refreshTarget({scope:'folder',folder_id:id}),
    onSettings: (id, trigger) => {void menus.openFolder(id, trigger);},
    onRead: (id, title, trigger) => {void folderActions.openCompletion(id, title, trigger);},
    onChanged: async (id, removed) => {await menus.folderChanged(id, removed); await changed();},
  });
  const tree = createReadingTree({
    readerApi, fetchMedia,
    onSelect: (selection,reason) => {
      newsActions.close(false);
      contentEpoch++;
      if(reason==='user')opened.clear();
      list.setArea(selection, readingCatalog);
      listOnly=true;
      void refresh();
    },
    onFeedMenu: (feed, folders, x, y, trigger) => {folderMenu.close(false);newsActions.close(false);feedActions.open(feed, folders, x, y, trigger);},
    onFolderMenu: (folder, catalog, x, y, trigger) => {feedActions.close(false);newsActions.close(false);folderMenu.open(folder, catalog, x, y, trigger);},
    onFeedSettings: (id, trigger) => menus.openFeed(id, trigger),
    onWarning: () => statusPanel.open(),
  });
  function stopUi(message: string) {
    if(stopped)return;
    checking?.abort();
    session.dispose();
    menus.stop();
    stopped = true;
    statusPanel.stop();
    contentEpoch++;
    opened.clear();
    list.clear();
    tree.clear();
    progress(message);
    onStopped();
    folderActions.stop();
    folderMenu.stop();
    feedActions.stop();
    newsActions.stop();

    void debug.refresh();
  }
  function readerMessage(data: ReaderMessage) {
    if (data === 'stop') stopUi('Oko stopped');
    else if (typeof data === 'object') {
      contentEpoch++;
      // Completion broadcasts invalidate the snapshot, not newly arrived items.
      refresh();
    } else {
      viewed(data, false);
      void refresh();
    }
  }
  function refresh() {return session.refresh();}
  async function updateReader() {
    if (stopped) return;
    checking?.abort();
    const controller=new AbortController();checking=controller;
    const epoch = contentEpoch;
    const sequence = ++refreshSequence;
    const active = () => !stopped && epoch === contentEpoch && sequence === refreshSequence;
    const debugRevision = debug.revision();
    const settingsRevision = menus.statusRevision();
    let statusAvailable = false;
    try {
      const selection=tree.selection;
      const sort=input('sort').value;
      const options:ReadingListOptions={target:selection.kind==='feed'?{scope:'feed',feed_id:selection.id!}:selection.kind==='folder'?{scope:'folder',folder_id:selection.id!}:{scope:'all'},sort:sort==='oldest'||sort==='importance'?sort:'newest',show_hidden:input('show-hidden').checked};
      const openedCheck=opened.poll();
      const selectedOnly=listOnly;listOnly=false;
      const frame=await resources.check(resources.plan(options,openedCheck.resources,selectedOnly),controller.signal);
      if(!active())return;
      const accepted=resources.accept(frame);
      if(accepted.reset){opened.reset();void refresh();return;}
      if(frame.state!=='current')return;
      opened.connected();
      if(openedCheck.session)opened.apply(frame.resources,openedCheck.session);
      const state=resources.status;
      if(!state)throw new Error('Reading status temporarily unavailable');
      const recovered=unavailable;unavailable=false;
      statusAvailable = true;
      onCollectionPoll();
      const statusConfirmed=frame.resources.some(resource=>resource.kind==='status'&&(resource.state==='changed'||resource.state==='unchanged'));
      // Local form actions can replace their own optimistic state after this
      // server version was accepted. Reapply confirmed cached status for the
      // new owner revision, without loading an unchanged form every poll.
      if(statusConfirmed&&(recovered||accepted.changed.has('status')||settingsAppliedRevision!==settingsRevision)){
        menus.status(state,settingsRevision);settingsAppliedRevision=settingsRevision;
      }
      if(recovered||accepted.changed.has('status')){
      debug.updateStatus(state.diagnostic_count, state.diagnostic_persistence_error||undefined, debugRevision);
      if (state.runtime_state==='stopped') { stopUi('Oko stopped'); return; }
      const enabled=state.runtime_state!=='disabled';
      onPowerState(enabled,state.runtime_state==='running'||state.runtime_state==='disabled');
      workAllowed=state.work_allowed;
      menus.workAllowed(state.work_allowed);
      opened.workAllowed(state.work_allowed);
      statusPanel.update(state.problems, state);
      debug.forgetMaterials(state.viewed_ids || []);
      statusPanel.forgetMaterials(state.viewed_ids || []);
      menus.sources(state.sources || []);
      }
      // A lost command response can remain pending while server status stays
      // unchanged. Every confirmed poll admits receipt reconciliation.
      if(statusConfirmed)void actions.flush();
      const navigation=resources.navigation();
      if(!navigation)throw new Error('Reading navigation temporarily unavailable');
      const navigationChanged=recovered||accepted.changed.has('navigation')||accepted.changed.has('feed');
      if(navigationChanged){
        readingCatalog=navigation;
        feedActions.update(navigation);
        folderMenu.update(navigation);
        folderActions.update(navigation.folders);
        if(tree.update(navigation,state.sources||[]))return;
        list.setArea(tree.selection,navigation);
      } else if(accepted.changed.has('status'))tree.update(navigation,state.sources||[]);
      if(state.runtime_state==='unavailable'){opened.disconnected();list.error(state.problems.current.find(p=>p.code==='storage_unavailable')?.message||'Storage unavailable');return;}
      const cards=resources.cards(options);
      if(!cards)throw new Error('Reading list temporarily unavailable');
      if(recovered||displayedList!==listIdentity(options)||navigationChanged||accepted.changed.has('list')||accepted.changed.has('card')){
        displayedList=listIdentity(options);
        opened.reconcile(cards);
        list.update(cards,opened.selection?.id);
        newsActions.update(list.context());
      }

    } catch (error) {
      if(controller.signal.aborted)return;
      unavailable=true;
      reportError(error, 'Oko interface');
      if (!active()) return;
      if (!statusAvailable) {workAllowed=false;onPowerState(true,false);menus.workAllowed(false);opened.workAllowed(false);opened.disconnected(); statusPanel.unavailable();}
      list.error(errorMessage(error));
      progress(statusAvailable ? 'Reading temporarily unavailable' : 'No connection to Oko');
    }
  }

  const session = createReaderSession({
    api: readerApi,
    onRefresh: updateReader,
    onMessage: readerMessage,
    onUnavailable: error => {
      reportError(error, 'Oko interface');
      if (!stopped) {
        onPowerState(true,false);menus.workAllowed(false);opened.workAllowed(false);
        opened.disconnected();
        statusPanel.unavailable();
        list.error(errorMessage(error));
        progress('No connection to Oko');
      }
    },
    onClosed: () => {newsActions.close(false); contentEpoch++; opened.clear(); list.clear();},
    onCloseError: error => reportError(error, 'Closing tab'),
  });
  input('sort').onchange = () => {newsActions.close(false); void changed();};
  input('show-hidden').onchange = () => {newsActions.close(false); void changed();};

  return {
    start: () => session.start(),
    stopAccess: () => stopUi(''),
    refresh,
    changed,
    openMaterial: (id: string) => opened.open({id}),
    resourceName: (resource: AffectedResource) => resource.kind === 'material' ? list.title(resource.material_id)
      : resource.kind === 'feed' ? readingCatalog.feeds.find(feed => feed.id === resource.feed_id)?.title
      : resource.kind === 'folder' ? readingCatalog.folders.find(folder => folder.id === resource.folder_id)?.title : undefined,
    async setPower(enabled:boolean) {
      try {await readerApi.setPower(enabled);}
      finally {contentEpoch++;session.post({kind:'power'});await refresh();}
    },
  };
}
