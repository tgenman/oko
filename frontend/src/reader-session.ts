
export type ReaderMessage = string | {kind:'power'} | {kind:'folder-viewed'; folder:string} | {kind:'feed-viewed'; feed:string};
type Options = {
  onRefresh: () => Promise<void>;
  onMessage: (message: ReaderMessage) => void;
  onClosed: () => void;
};

// Owns browser resources, not UI drafts or server execution. Disposing a React
// subscription never stops server work.
export function createReaderSession(options: Options) {
  let active = false, generation = 0, refreshSequence = 0;
  let refreshTimer: number | undefined;
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
        refreshTimer = window.setTimeout(() => void refresh(),1000);
      }
    }
  }
  function dispose() {
    active = false;
    generation++;
    window.clearTimeout(refreshTimer);
    bus?.close(); bus = undefined;
    window.removeEventListener('pagehide',pagehide);
    window.removeEventListener('pageshow',pageshow);
  }
  function pagehide() {
    options.onClosed();
    dispose();
    // A cached document still needs its restoration hook after ordinary
    // resources have been closed.
    window.addEventListener('pageshow',pageshow,{once:true});
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
        else if(data&&typeof data==='object'&&'kind'in data&&data.kind==='power')options.onMessage({kind:'power'});
        else if (data && typeof data === 'object' && 'kind' in data && data.kind === 'folder-viewed' && 'folder' in data && typeof data.folder === 'string') options.onMessage({kind:'folder-viewed',folder:data.folder});
        else if(data&&typeof data==='object'&&'kind'in data&&data.kind==='feed-viewed'&&'feed'in data&&typeof data.feed==='string')options.onMessage({kind:'feed-viewed',feed:data.feed});
      };
      window.addEventListener('pagehide',pagehide);
      window.addEventListener('pageshow',pageshow);
      // An immediate setup/cleanup/setup can cancel the first setup before it
      // sends any requests; only the surviving owner starts the loops.
      queueMicrotask(() => {if (active && stamp === generation) void refresh();});
    },
    refresh,
    post(message: ReaderMessage) {bus?.postMessage(message);},
    dispose,
  };
}
