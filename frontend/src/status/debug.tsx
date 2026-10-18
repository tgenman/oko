import { useState } from 'react';
import { mountSurface } from '../renderer';
import type { ReaderApi } from '../api/reader';
import { reportClientDiagnostic as debugBrowser, diagnosticEntries as debugEntries, clearDiagnostics as clearDebug } from '../api/generated/sdk.gen';
import type { DiagnosticEntry, ClientDiagnosticEvent as BrowserError } from '../api/generated/types.gen';
type Pending = Required<Omit<BrowserError, 'first_at' | 'occurred_at'>> & {
    first_at: string;
    occurred_at: string;
    sealed?: boolean;
};
type Entry = DiagnosticEntry & {
    pending?: boolean;
};
type ErrorLike = {
    name?: string;
    message?: unknown;
    stack?: string;
    diagnosticId?: string | null;
    materialIds?: string[];
};
function errorValue(value: unknown): ErrorLike { return value && typeof value === 'object' ? value : { message: value }; }
function byId(id: string) { return document.getElementById(id)!; }
function ErrorDetails({ entry, identity }: {
    entry: Entry;
    identity: string;
}) { const [open, setOpen] = useState(false); return <details data-key={identity} open={open} onToggle={event => setOpen(event.currentTarget.open)}><summary>{`${new Date(entry.last_at).toLocaleString()} · ${entry.source} · ${entry.message} · ×${entry.count}${entry.pending ? ' · not saved' : ''}`}</summary><p>{`First: ${new Date(entry.first_at).toLocaleString()} · Last: ${new Date(entry.last_at).toLocaleString()} · ${entry.error_type}`}</p><pre>{entry.traceback || entry.message}</pre></details>; }
let reporter: (error: unknown, source: string, newOccurrence: boolean) => void = () => { };
export function reportError(error: unknown, source = 'Browser', newOccurrence = false) { reporter(error, source, newOccurrence); }
export function createDebugPanel({ token, readerApi }: {
    token: string | null;
    readerApi: ReaderApi;
}) {
    const panel = byId('debug-panel');
    const seen = new WeakSet<object>();
    const deleted = new Set<string>();
    const retained = (entry: DiagnosticEntry | Pending) => !(entry.material_ids || []).some(id => deleted.has(id));
    const streamId = crypto.randomUUID();
    let sequence = 0;
    let remote: DiagnosticEntry[] = [], pending: Pending[] = [], sending = false, loading = false, revision = 0;
    let serverCount = 0, storageError = '', transportError = '';
    let clearing = false, delivery = Promise.resolve();
    let visible = false;
    function clean(value: unknown) {
        let text = String(value ?? '');
        if (token)
            text = text.replaceAll(token, '[session]');
        return text.replace(/(["']?(?:password|api[_-]?key|access[_-]?token|token|secret|authorization|session_string|string_session)["']?\s*[:=]\s*)(["'])(?:\\.|(?!\2)[\s\S])*\2/gi, '$1$2[hidden]$2')
            .replace(/((?:Bearer|Basic)\s+)[^\s"']+/gi, '$1[hidden]')
            .replace(/([?&#](?:token|api_key|access_token|password|secret)=)[^\s&#"']+/gi, '$1[hidden]')
            .replace(/(["']?(?:password|api[_-]?key|access[_-]?token|token|secret|authorization|session_string|string_session)["']?\s*[:=]\s*["']?)[^\s,;"'{}]+/gi, '$1[hidden]')
            .replace(/(https?:\/\/)[^/\s@]+@/g, '$1[hidden]@');
    }
    const key = (entry: Pick<DiagnosticEntry, 'source' | 'error_type' | 'message' | 'location'>) => JSON.stringify([entry.source, entry.error_type, entry.message, entry.location]);
    function render() {
        const groups = new Map<string, Entry>(remote.map(entry => [key(entry), { ...entry }]));
        for (const entry of pending) {
            const identity = key(entry), old = groups.get(identity);
            if (old) {
                if ((old.streams[entry.stream_id] ?? 0) < entry.sequence) {
                    old.count += entry.count;
                    old.last_at = old.last_at > entry.occurred_at ? old.last_at : entry.occurred_at;
                    old.first_at = old.first_at < entry.first_at ? old.first_at : entry.first_at;
                }
                old.pending = true;
            }
            else
                groups.set(identity, { ...entry, id: entry.event_id, streams: {}, first_at: entry.first_at, last_at: entry.occurred_at, pending: true });
        }
        const entries = [...groups.values()].sort((a, b) => b.last_at.localeCompare(a.last_at)).slice(0, 100);
        mountSurface(panel, <><div className="debug-heading"><h2>Exceptions <span id="debug-count">{Math.max(serverCount, entries.length)}</span></h2><button id="debug-clear" type="button" disabled={clearing} onClick={() => { void clear(); }}>Clear Error History</button></div><p>The last 100 error groups are saved locally and remain available after restart.</p><p id="debug-status" role="status">{storageError ? 'Journal not saved to disk: ' + clean(storageError) : transportError}</p><p id="debug-empty" hidden={entries.length > 0}>No errors yet</p><div id="debug-errors">{entries.map(entry => <ErrorDetails key={key(entry)} identity={key(entry)} entry={entry}/>)}</div></>);
    }
    function flush() {
        if (sending || clearing || !pending.length)
            return delivery;
        delivery = sendPending();
        return delivery;
    }
    async function sendPending() {
        sending = true;
        try {
            while (pending.length && !clearing) {
                const entry = pending[0]!;
                entry.sealed = true;
                const { sealed, ...body } = entry;
                const result = (await debugBrowser({ client: readerApi.client, body })).data;
                if (result.state==='accepted' && retained(result.entry)) {
                    const received = result.entry;
                    remote = remote.filter(group => group.id !== received.id);
                    remote.push(received);
                    revision++;
                }
                storageError = result.state==='persistence_failure'?result.message:'';
                if (result.state==='persistence_failure')
                    break;
                pending = pending.filter(item => item.event_id !== entry.event_id);
            }
            transportError = '';
        }
        catch (error) {
            transportError = clean(errorValue(error).message) + '. Errors are still available in this tab.';
        }
        finally {
            sending = false;
            render();
        }
    }
    reporter = (value, source, newOccurrence) => {
        const error = errorValue(value);
        if (error?.name === 'AbortError')
            return;
        if (value && typeof value === 'object') {
            if (newOccurrence)
                seen.delete(value);
            if (seen.has(value))
                return;
            seen.add(value);
        }
        // HTTP exception already has its server occurrence; do not count it twice.
        if (error?.diagnosticId) {
            void refresh();
            return;
        }
        const now = new Date().toISOString();
        const entry: Pending = { material_ids: [], event_id: crypto.randomUUID(), stream_id: streamId, sequence: ++sequence, source: clean(source).slice(0, 500),
            message: clean(error?.message ?? error).slice(0, 4000), traceback: clean(error?.stack || ''),
            error_type: clean(error?.name || 'Error').slice(0, 200),
            location: clean((error?.stack || '').split('\n').find(line => /^\s*at /.test(line))?.trim() || source).slice(0, 1000),
            first_at: now, occurred_at: now, count: 1 };
        entry.material_ids = error?.materialIds || [];
        const same = [...pending].reverse().find(item => key(item) === key(entry) && !item.sealed && item.count < 1000000);
        if (same) {
            same.count++;
            same.occurred_at = now;
        }
        else
            pending.push(entry);
        const keys = [...new Set([...pending].reverse().map(key))].slice(0, 100);
        pending = pending.filter(item => keys.includes(key(item)));
        render();
        void flush();
    };
    async function refresh() {
        void flush();
        if (!visible || loading || clearing)
            return;
        loading = true;
        const stamp = revision;
        try {
            const result = (await debugEntries({ client: readerApi.client })).data;
            const groups = result.groups;
            if (!Array.isArray(groups))
                throw new Error('Invalid journal response');
            if (stamp === revision) {
                remote = groups.filter(retained);
                serverCount = groups.length;
                storageError = result.persistence_error || '';
                transportError = '';
                render();
            }
        }
        catch (error) {
            if (stamp === revision) {
                transportError = 'Server journal unavailable: ' + clean(errorValue(error).message);
                render();
            }
        }
        finally {
            loading = false;
        }
    }
    async function clear() {
        if (clearing)
            return;
        clearing = true;
        render();
        const cutoff = sequence;
        for (const entry of pending)
            entry.sealed = true;
        revision++;
        try {
            // Finish an old delivery before deleting it; newer occurrences wait.
            await delivery;
            const result = (await clearDebug({ client: readerApi.client })).data;
            if (result.state==='persistence_failure')
                throw new Error(result.message);
            remote = [];
            pending = pending.filter(entry => entry.sequence > cutoff);
            serverCount = 0;
            storageError = '';
            transportError = '';
            revision++;
            render();
        }
        catch (error) {
            transportError = 'Could not clear history: ' + clean(errorValue(error).message);
            render();
        }
        finally {
            clearing = false;
            render();
            void flush();
        }
    }
    ;
    window.addEventListener('error', event => reportError(event.error || new Error(event.message || 'Resource failed to load'), event.filename || 'Browser', true));
    window.addEventListener('unhandledrejection', event => reportError(event.reason, 'Unhandled promise rejection', true));
    render();
    const timer = setInterval(refresh, 2000);
    window.addEventListener('pagehide', () => clearInterval(timer), { once: true });
    return { refresh, forgetMaterials(ids: string[]) {
            ids.forEach(id => deleted.add(id));
            if (remote.some(entry => !retained(entry)) || pending.some(entry => !retained(entry))) {
                remote = remote.filter(retained);
                pending = pending.filter(retained);
                render();
            }
        }, show(open: boolean) { visible = open; panel.hidden = !open; if (open)
            void refresh(); }, revision: () => revision, updateStatus(count: number | undefined, error: string | undefined, stamp: number) {
            if (clearing || stamp !== revision)
                return;
            if (typeof count === 'number')
                serverCount = count;
            if (typeof error === 'string')
                storageError = error;
            render();
        } };
}
