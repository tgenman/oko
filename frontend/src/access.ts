// All reader transports share this guard. A lost credential invalidates pending work.
export function createAccessGuard(adapter: typeof fetch, onLost:()=>void=()=>{}) {
  let locked = false, installed = false;
  const pending = new Set<AbortController>();
  let channel: BroadcastChannel | undefined;
  function lock() {
    if (locked) return;
    locked = true;
    onLost();
    for (const controller of pending) controller.abort();
    document.body.replaceChildren();
    location.replace('/login');
  }
  async function check() {
    try {
      const response = await adapter('/login/session', {cache:'no-store'});
      if (!response.ok) lock();
      else if (!locked) document.documentElement.style.visibility = '';
    } catch { lock(); }
  }
  function install() {
    if (installed) return;
    installed = true;
    channel = new BroadcastChannel('oko-access');
    channel.onmessage = () => lock();
    const form = document.createElement('form');
    form.method = 'post';form.action = '/logout';
    const button = document.createElement('button');
    button.type = 'submit';button.textContent = 'Выйти';
    form.append(button);
    document.querySelector('.reader-header-actions')?.append(form);
    form.addEventListener('submit', event => {
      event.preventDefault();
      void adapter('/logout', {method:'POST',redirect:'manual'}).then(response=>{
        if(response.type==='opaqueredirect'||response.status===303||response.ok){channel?.postMessage('logout');lock();}
      }).catch(()=>lock());
    });
    setInterval(()=>{if(!locked)void check();}, 5000);
    window.addEventListener('pagehide',()=>{document.documentElement.style.visibility='hidden';});
    window.addEventListener('pageshow',()=>{void check();});
    window.addEventListener('focus',()=>{void check();});
    document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void check();});
  }
  return {
    async fetch(input: RequestInfo | URL, options?: RequestInit): Promise<Response> {
      if (locked) throw new DOMException('Login required','AbortError');
      const controller = new AbortController();
      const signal = options?.signal ?? (input instanceof Request ? input.signal : undefined);
      const abort = ()=>controller.abort(signal?.reason);
      if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
      pending.add(controller);
      const release=()=>{pending.delete(controller);signal?.removeEventListener('abort',abort);};
      try {
        const response=await adapter(input,{...options,signal:controller.signal});
        if(response.headers.get('X-Oko-Login-Required')==='1')lock();
        if(locked)throw new DOMException('Login required','AbortError');
        if(response.headers.get('X-Oko-Password-Login')==='1')install();
        if (!installed || !response.body || [204,205,304].includes(response.status)) {release();return response;}
        const reader=response.body.getReader();
        const stream=new ReadableStream<Uint8Array>({
          async pull(target) {
            try {
              const result=await reader.read();
              if(locked)throw new DOMException('Login required','AbortError');
              if(result.done){release();target.close();}else target.enqueue(result.value);
            } catch(error){release();target.error(error);}
          },
          async cancel(reason){release();await reader.cancel(reason);},
        });
        return new Response(stream,{status:response.status,statusText:response.statusText,headers:response.headers});
      } catch(error){release();throw error;}
    },
  };
}
