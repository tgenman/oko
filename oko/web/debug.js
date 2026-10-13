let reporter = () => {};
export function reportError(error, source = 'Браузер', newOccurrence = false) { reporter(error, source, newOccurrence); }

export function createDebugPanel({token}) {
  const panel = document.getElementById('debug-panel'), list = document.getElementById('debug-errors');
  const status = document.getElementById('debug-status'), toggle = document.getElementById('debug-toggle');
  const seen = new WeakSet();
  const streamId = crypto.randomUUID();
  let sequence = 0;
  let remote = [], pending = [], sending = false, loading = false, revision = 0;
  let lastRender = '';
  let serverCount = 0, storageError = '', transportError = '';
  let clearing = false, delivery = Promise.resolve();
  const clearButton = document.getElementById('debug-clear');
  const headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'};
  function clean(value) {
    let text = String(value ?? '');
    if (token) text = text.replaceAll(token, '[session]');
    return text.replace(/(["']?(?:password|api[_-]?key|access[_-]?token|token|secret|authorization|session_string|string_session)["']?\s*[:=]\s*)(["'])(?:\\.|(?!\2)[\s\S])*\2/gi, '$1$2[hidden]$2')
      .replace(/((?:Bearer|Basic)\s+)[^\s"']+/gi, '$1[hidden]')
      .replace(/([?&#](?:token|api_key|access_token|password|secret)=)[^\s&#"']+/gi, '$1[hidden]')
      .replace(/(["']?(?:password|api[_-]?key|access[_-]?token|token|secret|authorization|session_string|string_session)["']?\s*[:=]\s*["']?)[^\s,;"'{}]+/gi, '$1[hidden]')
      .replace(/(https?:\/\/)[^/\s@]+@/g, '$1[hidden]@');
  }
  const key = entry => JSON.stringify([entry.source, entry.error_type, entry.message, entry.location]);
  function render() {
    const open = new Set([...list.querySelectorAll('details[open]')].map(node => node.dataset.key));
    const scroll = panel.scrollTop;
    const groups = new Map(remote.map(entry => [key(entry), {...entry}]));
    for (const entry of pending) {
      const identity = key(entry), old = groups.get(identity);
      if (old) {
        if (!((old.streams || {})[entry.stream_id] >= entry.sequence)) {
          old.count += entry.count;
          old.last_time = old.last_time > entry.occurred_at ? old.last_time : entry.occurred_at;
          old.first_time = old.first_time < entry.first_at ? old.first_time : entry.first_at;
        }
        old.pending = true;
      } else groups.set(identity, {...entry, first_time: entry.first_at, last_time: entry.occurred_at, pending: true});
    }
    const entries = [...groups.values()].sort((a, b) => b.last_time.localeCompare(a.last_time)).slice(0, 100);
    document.getElementById('debug-count').textContent = Math.max(serverCount, entries.length);
    status.textContent = storageError ? 'Журнал не сохранён на диск: ' + clean(storageError) : transportError;
    const snapshot = JSON.stringify(entries);
    if (snapshot === lastRender) return;
    lastRender = snapshot;
    list.replaceChildren(...entries.map(entry => {
      const details = document.createElement('details'); details.dataset.key = key(entry); details.open = open.has(key(entry));
      const summary = document.createElement('summary');
      summary.textContent = `${new Date(entry.last_time).toLocaleString()} · ${entry.source} · ${entry.message} · ×${entry.count}${entry.pending ? ' · не сохранено' : ''}`;
      const times = document.createElement('p');
      times.textContent = `Первое: ${new Date(entry.first_time).toLocaleString()} · Последнее: ${new Date(entry.last_time).toLocaleString()} · ${entry.error_type}`;
      const stack = document.createElement('pre'); stack.textContent = entry.traceback || entry.message;
      details.append(summary, times, stack); return details;
    }));
    panel.scrollTop = scroll;
    document.getElementById('debug-empty').hidden = entries.length > 0;
    status.textContent = storageError ? 'Журнал не сохранён на диск: ' + clean(storageError) : transportError;
  }
  function flush() {
    if (sending || clearing || !pending.length) return delivery;
    delivery = sendPending();
    return delivery;
  }
  async function sendPending() {
    sending = true;
    try {
      while (pending.length && !clearing) {
        const entry = pending[0]; entry.sealed = true;
        const {sealed, ...body} = entry;
        const response = await fetch('/api/debug', {method: 'POST', cache: 'no-store', headers, body: JSON.stringify(body)});
        if (!response.ok) throw new Error('Не удалось сохранить диагностику: HTTP ' + response.status);
        const result = await response.json();
        if (result.entry) {
          remote = remote.filter(group => group.id !== result.entry.id); remote.push(result.entry);
          revision++;
        }
        storageError = result.persistence_error || '';
        if (result.ok === false) break;
        pending = pending.filter(item => item.event_id !== entry.event_id);
      }
      transportError = '';
    } catch (error) { transportError = clean(error.message) + '. Ошибки пока доступны в этой вкладке.'; }
    finally { sending = false; render(); }
  }
  reporter = (error, source, newOccurrence) => {
    if (error?.name === 'AbortError') return;
    if (error && typeof error === 'object') {
      if (newOccurrence) seen.delete(error);
      if (seen.has(error)) return;
      seen.add(error);
    }
    // HTTP exception already has its server occurrence; do not count it twice.
    if (error?.diagnosticId) { void refresh(); return; }
    const now = new Date().toISOString();
    const entry = {event_id: crypto.randomUUID(), stream_id: streamId, sequence: ++sequence, source: clean(source).slice(0, 500),
      message: clean(error?.message ?? error).slice(0, 4000), traceback: clean(error?.stack || ''),
      error_type: clean(error?.name || 'Error').slice(0, 200),
      location: clean((error?.stack || '').split('\n').find(line => /^\s*at /.test(line))?.trim() || source).slice(0, 1000),
      first_at: now, occurred_at: now, count: 1};
    const same = [...pending].reverse().find(item => key(item) === key(entry) && !item.sealed && item.count < 1000000);
    if (same) { same.count++; same.occurred_at = now; }
    else pending.push(entry);
    const keys = [...new Set([...pending].reverse().map(key))].slice(0, 100);
    pending = pending.filter(item => keys.includes(key(item)));
    render(); void flush();
  };
  async function refresh() {
    void flush();
    if (panel.hidden || loading || clearing) return;
    loading = true;
    const stamp = revision;
    try {
      const response = await fetch('/api/debug', {cache: 'no-store', headers});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const result = await response.json();
      const groups = Array.isArray(result) ? result : result.groups;
      if (!Array.isArray(groups)) throw new Error('Некорректный ответ журнала');
      if (stamp === revision) {
        remote = groups;
        serverCount = groups.length; storageError = result.persistence_error || ''; transportError = ''; render();
      }
    } catch (error) { if (stamp === revision) {transportError = 'Журнал сервера недоступен: ' + clean(error.message); render();} }
    finally { loading = false; }
  }
  toggle.onchange = () => { panel.hidden = !toggle.checked; void refresh(); };
  clearButton.onclick = async () => {
    if (clearing) return;
    clearing = true; clearButton.disabled = true;
    const cutoff = sequence;
    for (const entry of pending) entry.sealed = true;
    revision++;
    try {
      // Finish an old delivery before deleting it; newer occurrences wait.
      await delivery;
      const response = await fetch('/api/debug', {method: 'DELETE', cache: 'no-store', headers});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const result = await response.json();
      if (!result.ok) throw new Error(result.persistence_error || 'Не удалось сохранить очистку журнала');
      remote = []; pending = pending.filter(entry => entry.sequence > cutoff);
      serverCount = 0; storageError = ''; transportError = ''; revision++; render();
    } catch (error) { transportError = 'Не удалось очистить историю: ' + clean(error.message); render(); }
    finally { clearing = false; clearButton.disabled = false; void flush(); }
  };
  window.addEventListener('error', event => reportError(event.error || new Error(event.message || 'Не загрузился ресурс'), event.filename || 'Браузер', true));
  window.addEventListener('unhandledrejection', event => reportError(event.reason, 'Необработанный Promise', true));
  render();
  const timer = setInterval(refresh, 2000);
  window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
  return {refresh, revision: () => revision, updateStatus(count, error, stamp) {
    if (clearing || stamp !== revision) return;
    if (typeof count === 'number') serverCount = count;
    if (typeof error === 'string') storageError = error;
    render();
  }};
}
