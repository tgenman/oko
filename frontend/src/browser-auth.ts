// One fetch owner shares a remembered credential across JSON and media calls.
export function browserFetch(adapter: typeof fetch, origin: string): typeof fetch {
  let remembered = false;
  let instance: string | null = null;
  let replaced = false;
  function replacement() {
    if (replaced) return;
    replaced = true;
    const notice = document.createElement('div');
    notice.setAttribute('role','alert');
    notice.style.cssText = 'position:fixed;inset:0 0 auto;z-index:10000;padding:16px;background:#fff3cd;color:#332700';
    notice.append('Oko was updated or restarted. Reload to continue. ');
    const reload = document.createElement('button');
    reload.textContent = 'Reload Oko';
    reload.addEventListener('click',()=>location.reload());
    notice.append(reload);
    document.body.append(notice);
  }
  return async (input, options) => {
    const url = new URL(input instanceof Request ? input.url : String(input), origin);
    if (url.origin !== origin) throw new Error('Unexpected reader address');
    const headers = new Headers(options?.headers ?? (input instanceof Request ? input.headers : undefined));
    if (replaced) throw new Error('Oko was restarted. Reload to continue.');
    if (remembered) headers.delete('Authorization');
    if (!remembered && url.pathname === '/api/v3/status' && headers.has('Authorization')) headers.set('X-Oko-Remember','1');
    const response = await adapter(input, {...options, headers});
    const current = response.headers.get('X-Oko-Instance');
    if (current && instance && current !== instance) {
      replacement();
      throw new Error('Oko was restarted. Reload to continue.');
    }
    if (current) instance = current;
    if (url.pathname === '/api/v3/status' && response.ok && response.headers.get('X-Oko-Browser-Session') === 'remembered') {
      remembered = true;
      const fragment = new URLSearchParams(location.hash.slice(1));
      if (fragment.has('token')) {
        fragment.delete('token');
        history.replaceState(history.state, '', location.pathname + location.search + (fragment.size ? '#'+fragment : ''));
      }
    }
    return response;
  };
}
