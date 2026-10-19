import {completeFeed, completeFolder, previewFeedCompletion, previewCompletion} from '../api/generated/sdk.gen';
import type {CompletionPreview} from '../api/generated/types.gen';
import {ReaderError, type ReaderApi} from '../api/reader';
import {reportError} from '../status/debug';
import {createStore, errorMessage} from './store';

export type CompletionTarget = {scope: 'feed' | 'folder'; id: string};
type State = {
  target: CompletionTarget | null;
  preview: CompletionPreview | null;
  loading: boolean;
  busy: boolean;
  canConfirm: boolean;
  message: string;
};
const empty: State = {target: null, preview: null, loading: false, busy: false, canConfirm: false, message: ''};
const key = (target: CompletionTarget) => target.scope + '/' + target.id;

function changedComposition(error: unknown): CompletionPreview | null {
  if (!(error instanceof ReaderError) || error.code !== 'composition_changed') return null;
  const detail = error.detail;
  if (!detail || typeof detail !== 'object' || !('preview' in detail)) return null;
  const preview = detail.preview;
  if (!preview || typeof preview !== 'object'
    || !('token' in preview) || typeof preview.token !== 'string'
    || !('cards_count' in preview) || typeof preview.cards_count !== 'number'
    || !('publications_count' in preview) || typeof preview.publications_count !== 'number') return null;
  return {token: preview.token, cards_count: preview.cards_count, publications_count: preview.publications_count};
}

// A confirmation owns its presentation lifetime; pending commands survive closing
// and reopening it. Command outcomes still belong to the existing action tracker.
export function createCompletion({readerApi, isStopped, onSettled, onConfirmed}: {
  readerApi: ReaderApi;
  isStopped: () => boolean;
  onSettled: (target: CompletionTarget) => void;
  onConfirmed?: () => void;
}) {
  const store = createStore<State>(empty);
  const pending = new Set<string>();
  let opening = {};
  const active = (stamp: object) => opening === stamp && !isStopped();
  function update(patch: Partial<State> = {}) {
    const next = {...store.get(), ...patch};
    const busy = next.loading || !!next.target && pending.has(key(next.target));
    store.set({...next, busy, canConfirm: !!next.target && !!next.preview?.publications_count && !busy && !isStopped()});
  }
  function close() {opening = {}; update(empty);}
  async function open(target: CompletionTarget) {
    if (isStopped()) return;
    const stamp = opening = {};
    update({...empty, target, loading: true});
    try {
      const {data} = target.scope === 'feed'
        ? await previewFeedCompletion({client: readerApi.client, path: {feed: target.id}})
        : await previewCompletion({client: readerApi.client, path: {folder: target.id}});
      if (active(stamp)) update({preview: data, loading: false});
    } catch (error) {
      reportError(error, 'Reading completion preview');
      if (active(stamp)) update({loading: false, message: errorMessage(error)});
    }
  }
  async function confirm() {
    const {target, preview, canConfirm} = store.get();
    if (!target || !preview || !canConfirm || isStopped()) return;
    const stamp = opening;
    pending.add(key(target));
    update({message: ''});
    try {
      if (target.scope === 'feed') {
        await readerApi.command({operation: 'complete_feed', feed_id: target.id}, headers =>
          completeFeed({client: readerApi.client, headers, path: {feed: target.id}, body: {token: preview.token}}));
      } else {
        await readerApi.command({operation: 'complete_folder', folder_id: target.id}, headers =>
          completeFolder({client: readerApi.client, headers, path: {folder: target.id}, body: {token: preview.token}}));
      }
      if (active(stamp)) {close(); onConfirmed?.();}
    } catch (error) {
      reportError(error, 'Reading completion');
      if (active(stamp)) {
        const changed = changedComposition(error);
        const unknown = !(error instanceof ReaderError) || !error.confirmed;
        update({preview: changed || (unknown ? null : preview), message: unknown
          ? 'Action outcome unknown. Check Status. Close and reopen to confirm the current items; this action will not be retried.'
          : errorMessage(error)});
      }
    } finally {
      pending.delete(key(target));
      update();
      // Even an error can follow a committed viewed_set. Reconcile authoritative
      // cards instead of clearing an entire feed/folder or assuming rollback.
      onSettled(target);
    }
  }
  return {get: store.get, subscribe: store.subscribe, open, confirm, close,
    pending: (target: CompletionTarget) => pending.has(key(target))};
}
