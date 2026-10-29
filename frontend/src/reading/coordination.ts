import {createReadingResources,listIdentity} from './resources';
import type {ReadingListOptions} from './resources';
import {createCardPages} from './card-pages';
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
  const pages=createCardPages(readerApi);
  let currentOptions:ReadingListOptions|undefined;
  let moreLoading:{key:string;controller:AbortController;promise:Promise<void>}|undefined;
  let checking:AbortController|undefined;
  let unavailable=false;
  let displayedList:string|undefined;
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
  function invalidate() {
    contentEpoch++;
    checking?.abort();
    moreLoading?.controller.abort();
    moreLoading=undefined;
  }
  const input = (id: string) => document.getElementById(id) as HTMLInputElement;

  function viewed(id: string, broadcast: boolean) {
    invalidate();
    pages.forget([id]);
    debug.forgetMaterials([id]);
    statusPanel.forgetMaterials([id]);
    opened.viewed(id);
    list.removed(id);
    if (broadcast) session.post(id);
  }
  function changed() {
    invalidate();
    return refresh();
  }
  const opened = createOpenedMaterial({
    readerApi,
    getFeed:id=>readingCatalog.feeds.find(feed=>feed.id===id),
    getStorage:()=>resources.storageId,
    fetchMedia: (path, signal) => fetchMedia(path, signal, 'no-store'),
    onViewed: id => { viewed(id, true); void refresh(); },
    onChanged: contentChanged => {if (contentChanged) invalidate(); void refresh();},
  });
  const list = createMaterialList({
    readerApi, fetchMedia,
    onContext: (card, context, x, y, trigger) => {folderMenu.close(false);feedActions.close(false);newsActions.open(card, context, x, y, trigger);},
    onOpen: card => opened.open(card),
    onRetry: refresh,
    onMore: () => {
      if(!currentOptions)return Promise.resolve();
      const options=currentOptions,key=listIdentity(options);
      if(moreLoading?.key===key)return moreLoading.promise;
      moreLoading?.controller.abort();
      const epoch=contentEpoch,controller=new AbortController();
      const operation={key,controller,promise:Promise.resolve()};
      operation.promise=(async()=>{
        try {await pages.more(options,controller.signal);if(epoch===contentEpoch)await refresh();}
        catch(error){if(epoch===contentEpoch&&!controller.signal.aborted){if(error instanceof Error&&error.message==='cursor_expired'){pages.reset(options);list.clear();await refresh();}else{reportError(error,'Loading items');list.error(errorMessage(error));}}}
      })().finally(()=>{if(moreLoading===operation)moreLoading=undefined;});
      moreLoading=operation;
      return operation.promise;
    },
    onNew: () => {if(currentOptions){const options=currentOptions;pages.reset(options);if(options.sort==='oldest'){pages.reset({...options,sort:'newest'});list.newest();}list.top();void changed();}},
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
      invalidate();
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
      invalidate();
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
      invalidate();
      if(reason==='user')opened.clear();
      list.setArea(selection, readingCatalog);
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
    invalidate();
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
      invalidate();
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
      const options:ReadingListOptions={target:selection.kind==='feed'?{scope:'feed',feed_id:selection.id!}:selection.kind==='folder'?{scope:'folder',folder_id:selection.id!}:{scope:'all'},sort:sort==='oldest'?sort:'newest',show_hidden:input('show-hidden').checked};
      currentOptions=options;
      opened.refresh();
      const pageResult=pages.poll(options,list.visible(),list.atTop(),controller.signal)
        .then(page=>({page}),error=>({error}));
      const reply=await resources.refresh(controller.signal,statusPanel.isOpen()||menus.isOpen());
      if(!active())return;
      const accepted=resources.accept(reply);
      if(accepted.reset){pages.clear();list.clear();opened.reset();void refresh();return;}
      opened.connected();
      const state=resources.status;
      if(!state)throw new Error('Reading status temporarily unavailable');
      const recovered=unavailable;unavailable=false;
      statusAvailable = true;
      onCollectionPoll();
      const statusConfirmed=true;
      const details=resources.details;
      // Local form actions can replace their own optimistic state after this
      // server version was accepted. Reapply confirmed cached status for the
      // new owner revision, without loading an unchanged form every poll.
      if(menus.isOpen()&&details&&!accepted.detailsError&&(recovered||accepted.changed.has('details')||settingsAppliedRevision!==settingsRevision)){
        menus.status(details,settingsRevision);settingsAppliedRevision=settingsRevision;
      }
      if(recovered||accepted.changed.has('status')){
      debug.updateStatus(state.diagnostic_count, state.diagnostic_persistence_error||undefined, debugRevision);
      if (state.runtime_state==='stopped') { stopUi('Oko stopped'); return; }
      const enabled=state.runtime_state!=='disabled';
      onPowerState(enabled,state.runtime_state==='running'||state.runtime_state==='disabled');
      workAllowed=state.work_allowed;
      menus.workAllowed(state.work_allowed);
      opened.workAllowed(state.work_allowed);
      statusPanel.summary(state);
      debug.forgetMaterials(state.viewed_ids || []);
      statusPanel.forgetMaterials(state.viewed_ids || []);
      menus.sources(state.sources || []);
      }
      if(statusPanel.isOpen()){
        if(accepted.detailsError)statusPanel.detailsFailed(errorMessage(accepted.detailsError));
        else if(details)statusPanel.update(details.problems,details);
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
      if(state.runtime_state==='unavailable'){opened.disconnected();list.error('Storage unavailable');return;}
      const result=await pageResult;
      if(!active())return;
      if('error' in result)throw result.error;
      const page=result.page;
      const cards=page.items.flatMap(card=>{const feed=navigation.feeds.find(feed=>feed.id===card.feed_id);return feed?[{...card,feed_title:feed.title,folder_id:feed.folder_id}]:[];});
      if(recovered||displayedList!==listIdentity(options)||navigationChanged||page.changed){
        displayedList=listIdentity(options);page.changed=false;
        opened.reconcile(cards);
        list.update(cards,opened.selection?.id,page.total,page.next!==null,page.newItems);
        newsActions.update(list.context());
      }

    } catch (error) {
      if(controller.signal.aborted)return;
      if(error instanceof Error&&error.message==='cursor_expired'&&currentOptions){pages.reset(currentOptions);list.clear();void refresh();return;}
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
    onClosed: () => {newsActions.close(false); invalidate(); opened.clear(); list.clear();},
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
      finally {invalidate();session.post({kind:'power'});await refresh();}
    },
  };
}
