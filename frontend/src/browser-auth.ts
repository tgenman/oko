// One fetch owner shares a remembered credential across JSON and media calls.
export function browserFetch(adapter: typeof fetch, origin: string): typeof fetch {
  let remembered = false;
  return async (input, options) => {
    const url = new URL(input instanceof Request ? input.url : String(input), origin);
    if (url.origin !== origin) throw new Error('Unexpected reader address');
    const headers = new Headers(options?.headers ?? (input instanceof Request ? input.headers : undefined));
    if (remembered) headers.delete('Authorization');
    const response = await adapter(input, {...options, headers});
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
