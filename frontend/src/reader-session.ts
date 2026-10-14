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
  const tab = crypto.randomUUID();
  let active = false, generation = 0;
  let heartbeatTimer: number | undefined, refreshTimer: number | undefined;
  let bus: BroadcastChannel | undefined;
  async function refresh(): Promise<void> {
    if (!active) return;
    window.clearTimeout(refreshTimer);
    const stamp = generation;
    try {await options.onRefresh();}
    finally {
      if (active && stamp === generation) {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(() => void refresh(),500);
      }
    }
  }
  async function beat(stamp: number): Promise<void> {
    if (!active || stamp !== generation) return;
    try {await options.api.heartbeat(tab);}
    catch(error) {if (active && stamp === generation) options.onUnavailable(error);}
    if (active && stamp === generation) heartbeatTimer = window.setTimeout(() => void beat(stamp),2000);
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
    void options.api.close(tab).catch(options.onCloseError);
    options.onClosed();
    dispose();
  }
  function pageshow(event: PageTransitionEvent) {if (event.persisted) location.reload();}
  return {
    start() {
      if (active) return;
      active = true;
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
