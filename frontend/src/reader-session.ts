import {ReaderError} from './api/errors';
import type {ClientSession} from './api/generated/types.gen';
import type {ReaderApi} from './api/reader';

export type ReaderMessage = string | {kind:'folder-viewed'; folder:string};
type Options = {
  api: ReaderApi;
  onRefresh: () => Promise<void>;
  onMessage: (message: ReaderMessage) => void;
  onUnavailable: (error: unknown) => void;
  onClosed: () => void;
  onCloseError: (error: unknown) => void;
};

// Owns browser resources, not UI drafts or server execution. Disposing a React
// subscription never means closing the reader lease or stopping server work.
export function createReaderSession(options: Options) {
  let session: ClientSession | undefined;
  let creating: Promise<ClientSession> | undefined;
  let closed = false;
  let active = false, generation = 0, refreshSequence = 0;
  let heartbeatTimer: number | undefined, refreshTimer: number | undefined;
  let bus: BroadcastChannel | undefined;
  async function refresh(): Promise<void> {
    if (!active) return;
    window.clearTimeout(refreshTimer);
    const stamp = generation;
    const sequence = ++refreshSequence;
    try {await options.onRefresh();}
    finally {
      // Only the latest refresh owns the next poll. An older request may
      // finish after a selection change, while its replacement is still loading.
      if (active && stamp === generation && sequence === refreshSequence) {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(() => void refresh(),500);
      }
    }
  }
  async function beat(stamp: number): Promise<void> {
    if (!active || stamp !== generation) return;
    let delay = 2000;
    try {
      const status = await options.api.lifetime();
      if (!active || stamp !== generation) return;
      if (status.lifetime.kind === 'explicit') return;
      delay = status.lifetime.renew_after_seconds * 1000;
      if (session) {
        try {session = await options.api.renewSession(session.id);}
        catch (error) {
          if (!(error instanceof ReaderError) || error.code !== 'client_session_expired') throw error;
          session = undefined;
        }
      }
      if (!session) {
        creating ??= options.api.createSession().finally(() => {creating=undefined;});
        session = await creating;
      }
      delay = session.renew_after_seconds * 1000;
      if (closed) {void options.api.closeSession(session.id).catch(options.onCloseError);session=undefined;}
    } catch(error) {if (active && stamp === generation) options.onUnavailable(error);}
    if (active && stamp === generation) heartbeatTimer = window.setTimeout(() => void beat(stamp),delay);
  }
  function dispose() {
    active = false;
    generation++;
    window.clearTimeout(refreshTimer);
    window.clearTimeout(heartbeatTimer);
    bus?.close(); bus = undefined;
    window.removeEventListener('pagehide',pagehide);
    window.removeEventListener('pageshow',pageshow);
  }
  function pagehide() {
    closed = true;
    if (session) void options.api.closeSession(session.id).catch(options.onCloseError);
    session = undefined;
    options.onClosed();
    dispose();
    // A cached document still needs its restoration hook after ordinary
    // resources and the reader lease have been closed.
    window.addEventListener('pageshow',pageshow,{once:true});
  }
  function pageshow(event: PageTransitionEvent) {if (event.persisted) location.reload();}
  return {
    start() {
      if (active) return;
      active = true;
      closed = false;
      const stamp = ++generation;
      bus = new BroadcastChannel('oko-reader');
      bus.onmessage = event => {
        const data: unknown = event.data;
        if (typeof data === 'string') options.onMessage(data);
        else if (data && typeof data === 'object' && 'kind' in data && data.kind === 'folder-viewed' && 'folder' in data && typeof data.folder === 'string') options.onMessage({kind:'folder-viewed',folder:data.folder});
      };
      window.addEventListener('pagehide',pagehide);
      window.addEventListener('pageshow',pageshow);
      // An immediate setup/cleanup/setup can cancel the first setup before it
      // sends any requests; only the surviving owner starts the loops.
      queueMicrotask(() => {if (active && stamp === generation) void beat(stamp).then(() => {if (active && stamp === generation) void refresh();});});
    },
    refresh,
    post(message: ReaderMessage) {bus?.postMessage(message);},
    dispose,
  };
}
