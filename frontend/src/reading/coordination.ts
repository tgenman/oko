import {createNewsActions} from './news-actions';
import {createMaterialList} from './list';
import {createReadingTree, type Navigation} from './tree';
import {createOpenedMaterial} from './opened';
import {createFeedActions} from './feed-actions';
import {createFolderActions} from './folder-actions';
import {createReaderSession, type ReaderMessage} from '../reader-session';
import {reportError, type createDebugPanel} from '../status/debug';
import {workText} from '../status/recovery';
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
  onStopped: () => void;
};

// Coordinates accepted reading changes. Local owners retain DOM, scroll, drafts
// and media; the session alone owns polling, presence and browser resources.
export function createReadingCoordination({readerApi, fetchMedia, debug, menus, statusPanel, actions, progress, onStopped}: Options) {
  let contentEpoch = 0;
  let refreshSequence = 0;
  let stopped = false;
  let readingCatalog: Navigation = {folders: [], feeds: [], count: 0};
  const input = (id: string) => document.getElementById(id) as HTMLInputElement;

  function viewed(id: string, broadcast: boolean) {
    contentEpoch++;
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
    fetchMedia: (path, signal) => fetchMedia(path, signal, 'no-store'),
    onViewed: id => { viewed(id, true); void refresh(); },
    onChanged: contentChanged => {if (contentChanged) contentEpoch++; void refresh();},
  });
  const list = createMaterialList({
    readerApi, fetchMedia,
    onContext: (card, context, x, y, trigger) => newsActions.open(card, context, x, y, trigger),
    onOpen: card => opened.open(card),
    onRetry: refresh,
  });
  const newsActions = createNewsActions({
    readerApi, isStopped: () => stopped,
    onViewed: id => viewed(id, true),
    onRefresh: changed,
    onPreview: ids => list.preview(ids),
  });
  const folderActions = createFolderActions({
    readerApi, isStopped: () => stopped,
    getFolder: () => tree.selection.kind === 'folder' ? tree.selection.id : null,
    onRefresh: refresh,
    onCompleted: folder => {
      contentEpoch++;
      opened.folderCompleted(folder);
      if (tree.selection.kind === 'folder' && tree.selection.id === folder) list.clear();
      session.post({kind: 'folder-viewed', folder});
    },
  });
  list.bindCommands(folderActions);
  const feedActions = createFeedActions({
    readerApi, isStopped: () => stopped,
    onSettings: (id, trigger) => {void menus.openFeed(id, trigger);},
    onChanged: changed,
    onCompleted: feed => {
      // A completion token cannot cover later arrivals. Reconcile the accepted
      // cards on refresh instead of clearing everything belonging to this feed.
      contentEpoch++;
      session.post({kind: 'feed-viewed', feed});
    },
    onMove: id => menus.feedMoved(id),
    removeFeed: id => menus.removeFeed(id),
  });
  const tree = createReadingTree({
    readerApi, fetchMedia,
    onSelect: selection => {
      newsActions.close(false);
      contentEpoch++;
      opened.clear();
      list.setArea(selection, readingCatalog);
      void refresh();
    },
    onFeedMenu: (feed, folders, x, y, trigger) => feedActions.open(feed, folders, x, y, trigger),
    onFeedSettings: (id, trigger) => menus.openFeed(id, trigger),
    onWarning: () => statusPanel.open(),
  });
  function stopUi(message: string) {
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
    feedActions.stop();
    newsActions.stop();

    void debug.refresh();
  }
  function readerMessage(data: ReaderMessage) {
    if (data === 'stop') stopUi('Oko stopped');
    else if (typeof data === 'object') {
      contentEpoch++;
      if (data.kind === 'folder-viewed') opened.folderCompleted(data.folder);
      refresh();
    } else {
      viewed(data, false);
      void refresh();
    }
  }
  function refresh() {return session.refresh();}
  async function updateReader() {
    if (stopped) return;
    const epoch = contentEpoch;
    const sequence = ++refreshSequence;
    const active = () => !stopped && epoch === contentEpoch && sequence === refreshSequence;
    const debugRevision = debug.revision();
    const settingsRevision = menus.statusRevision();
    let statusAvailable = false;
    try {
      const state = await readerApi.status();
      if (!active()) return;
      statusAvailable = true;
      debug.updateStatus(state.diagnostic_count, state.diagnostic_persistence_error||undefined, debugRevision);
      menus.status(state,settingsRevision);
      if (state.runtime_state==='stopped') { stopUi('Oko stopped'); return; }
      statusPanel.update(state.problems, state);
      debug.forgetMaterials(state.viewed_ids || []);
      statusPanel.forgetMaterials(state.viewed_ids || []);
      void actions.flush();
      progress(`${workText(state)} · sources ${state.ready_sources}/${state.total_sources}`);
      menus.sources(state.sources || []);
      const navigation = await tree.load();
      if (!active()) return;
      readingCatalog = navigation;
      feedActions.update(navigation);
      if (tree.update(navigation, state.sources || [])) return;
      list.setArea(tree.selection, navigation);
      if (state.runtime_state==='unavailable') { opened.clear(); list.error(state.problems.current.find(p=>p.code==='storage_unavailable')?.message||'Storage unavailable'); return; }
      const params = new URLSearchParams({sort: input('sort').value});
      if (input('show-hidden').checked) params.set('show_hidden', 'true');
      const selection = tree.selection;
      if (selection.kind === 'folder' && selection.id) params.set('folder', selection.id);
      if (selection.kind === 'feed' && selection.id) params.set('feed', selection.id);
      const cards = await list.load(params);
      if (!active()) return;
      opened.reconcile(cards);
      list.update(cards, opened.selection?.id);
      newsActions.update(list.context());

    } catch (error) {
      reportError(error, 'Oko interface');
      if (!active()) return;
      if (!statusAvailable) {opened.clear(); statusPanel.unavailable();}
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
        opened.clear();
        statusPanel.unavailable();
        list.error(errorMessage(error));
        progress('No connection to Oko');
      }
    },
    onClosed: () => {newsActions.close(false); contentEpoch++; opened.clear(); list.clear();},
    onCloseError: error => reportError(error, 'Closing tab'),
  });
  input('sort').onchange = () => {newsActions.close(false); void changed();};
  input('show-hidden').onchange = () => {newsActions.close(false); opened.clear(); void changed();};

  return {
    start: () => session.start(),
    refresh,
    changed,
    openMaterial: (id: string) => opened.open({id}),
    resourceName: (resource: AffectedResource) => resource.kind === 'material' ? list.title(resource.material_id)
      : resource.kind === 'feed' ? readingCatalog.feeds.find(feed => feed.id === resource.feed_id)?.title
      : resource.kind === 'folder' ? readingCatalog.folders.find(folder => folder.id === resource.folder_id)?.title : undefined,
    async stop() {
      await readerApi.stop();
      session.post('stop');
      stopUi('Oko stopped');
    },
  };
}
