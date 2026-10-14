import { useState, type ReactNode } from 'react';
import { mountSurface } from '../renderer';
import type { Problem, ProblemAction, ProblemSnapshot, Status } from '../api/generated/types.gen';
import type { ReaderActions, VisibleProblem } from './actions';
type Debug = {
    show(open: boolean): void;
};
type Options = {
    debug: Debug;
    actions: ReaderActions;
    onSettings(target: ProblemAction, trigger: HTMLElement): Promise<void>;
    onMaterial(id: string): void;
    onRetry(target: ProblemAction, problem: Problem): Promise<void>;
    onRefresh(): void;
    onAvailability?(available:boolean):void;
};
const names: Record<string, string> = { settings: 'Настроить модель', 'feed-settings': 'Настройки фида', retry: 'Повторить', 'theses-retry': 'Повторить обработку', 'filter-retry': 'Повторить фильтрацию', regenerate: 'Пересоздать обзор', material: 'Показать материал', objects: 'Показать материалы', collect: 'Обновить источники' };
const sections = [['action', 'Нужно действие'], ['wait', 'Ожидание и работа'], ['object', 'Проблемы материалов и фидов']] as const;
const emptySnapshot: ProblemSnapshot = { current: [], recent: [], count: 0, updated_at: 0 };
function byId(id: string) { return document.getElementById(id)!; }
// Stable card identity owns disclosure; polling updates props without rebuilding
// the focused controls. Group membership is explicit presentation state.
function Card({ problem, disabled, onAction, onMaterial, onDisclosure }: {
    problem: VisibleProblem;
    disabled: boolean;
    onAction(target: ProblemAction, trigger: HTMLElement): void;
    onMaterial(id: string): void;
    onDisclosure(open: boolean): void;
}) {
    const [expanded, setExpanded] = useState(false);
    const finished = problem.state === 'finished';
    const tag = problem.saved === false ? 'Не сохранено' : finished ? problem.outcome === 'irrelevant' ? 'Больше не актуально' : 'Устранено' : problem.state === 'retrying' ? 'Повторяем…' : '';
    return <div className={'status-problem' + (finished ? ' status-finished' : '')} data-problem-id={problem.id} data-section={problem.section}>
    <span className="status-icon" aria-hidden="true">{finished ? '✓' : problem.section === 'wait' ? '◷' : '!'}</span><div className="status-problem-content">
      <small className="status-tag" hidden={!tag}>{tag}</small><h3>{problem.title}</h3><p>{problem.consequence}</p><p className="status-next">{problem.next_step}{problem.next_at ? ' · ' + new Date(problem.next_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}</p>
      <button className="status-action" hidden={!problem.action || finished} disabled={disabled || problem.state === 'retrying'} onClick={event => { const action = problem.action; if (!action)
        return; if (action.kind === 'objects') {
        setExpanded(true);
        onDisclosure(true);
    }
    else
        onAction(action, event.currentTarget); }}>{problem.action?.kind === 'settings' && problem.code !== 'codex-choice' ? 'Открыть настройки' : names[problem.action?.kind || ''] || 'Открыть настройки'}</button>
      <details className="status-details" open={expanded} onToggle={event => { const open = event.currentTarget.open; if (open !== expanded) {
        setExpanded(open);
        onDisclosure(open);
    } }}><summary>Подробности</summary><p hidden={!problem.reason}>{problem.reason || ''}</p><div>{problem.objects.map(object => <button key={object.kind + ':' + object.id} className="status-object" disabled={disabled || (object.kind !== 'material' && !problem.action)} onClick={event => { if (object.kind === 'material')
        onMaterial(object.id);
    else if (problem.action)
        onAction(problem.action, event.currentTarget); }}>{object.label}</button>)}</div></details>
    </div></div>;
}
function Recent({count,children}: {count:number;children:ReactNode}) {
    const [open,setOpen]=useState(false);
    return <details className="status-recent" hidden={!count} open={open} onToggle={event=>setOpen(event.currentTarget.open)}>
        <summary>Недавно завершённые · {count}</summary><div>{children}</div>
    </details>;
}
export function createStatusPanel({ debug, actions, onSettings, onMaterial, onRetry, onRefresh,onAvailability }: Options) {
    const panel = byId('status-panel'), toggle = byId('status-toggle'), body = byId('status-body');
    const deleted = new Set<string>(), disclosed = new Set<string>(), owners = new Map<string, string>();
    let snapshot = emptySnapshot, online: boolean | null = null, stopped = false, opener: HTMLElement | null = null, work = '';
    const debugPanel = byId('debug-panel');
    byId('status-journal').append(debugPanel);
    function focusMode() {
        const modal = matchMedia('(max-width:700px)').matches;
        panel.setAttribute('role', modal ? 'dialog' : 'complementary');
        if (modal)
            panel.setAttribute('aria-modal', 'true');
        else
            panel.removeAttribute('aria-modal');
        byId('reader-shell').inert = modal && !panel.hidden;
    }
    function close(restore = true) { panel.hidden = true; toggle.setAttribute('aria-expanded', 'false'); focusMode(); debug.show(false); if (restore && opener?.isConnected)
        opener.focus({ preventScroll: true }); }
    function material(id: string) { close(false); onMaterial(id); }
    async function act(target: ProblemAction, problem: Problem, trigger: HTMLElement) {
        if (['settings', 'feed-settings', 'add'].includes(target.kind)) {
            byId('reader-shell').inert = false;
            await onSettings(target, trigger);
            document.querySelector('dialog[open]')?.addEventListener('close', () => { focusMode(); if (!panel.hidden)
                byId('status-close').focus(); }, { once: true });
        }
        else if (target.kind === 'material' && target.id)
            material(target.id);
        else
            try {
                await onRetry(target, problem);
            }
            catch { /* API owner tracks the outcome. */ }
    }
    function statePage() { body.hidden = false; byId('status-journal').hidden = true; byId('status-journal-back').hidden = true; debug.show(false); }
    function journal() { body.hidden = true; byId('status-journal').hidden = false; byId('status-journal-back').hidden = false; debug.show(true); byId('status-journal-back').focus(); }
    function render() {
        const shown = actions.merge(snapshot), all = [...shown.current, ...shown.recent], wanted = new Set(all.map(p => p.id));
        for (const id of owners.keys())
            if (!wanted.has(id)) {
                owners.delete(id);
                disclosed.delete(id);
            }
        for (const problem of all) {
            const previous = owners.get(problem.id);
            const held = problem.state === 'finished' && disclosed.has(problem.id) && previous && previous !== 'recent';
            owners.set(problem.id, held ? previous : problem.state === 'finished' ? 'recent' : problem.section);
        }
        const grouped = (key: string) => [...owners.keys()].flatMap(id => { const p = all.find(p => p.id === id); return owners.get(id) === key && p ? [p] : []; });
        mountSurface(toggle, <>{stopped ? 'Око остановлено' : !online ? 'Нет связи с Око' : shown.count ? `Проблемы · ${shown.count}` : 'Состояние'}</>);
        toggle.classList.toggle('has-problems', shown.count > 0 || !online);
        mountSurface(byId('status-summary'),<>{stopped ? 'Око остановлено' : !online ? 'Последние известные сведения' : shown.count ? `Требуют внимания: ${shown.count}` : 'Проблем нет · можно читать'}</>);
        byId('status-connection').hidden = Boolean(online) || stopped;
        mountSurface(byId('status-connection'), <><p>{snapshot.updated_at ? 'Нет связи с Око · Состояние может быть устаревшим. Последнее обновление: ' + new Date(snapshot.updated_at * 1000).toLocaleTimeString() : 'Состояние Око пока неизвестно. Ожидаем первого ответа.'}</p><button id="status-check-connection" onClick={onRefresh}>Проверить связь</button></>);
        onAvailability?.(!stopped && Boolean(online));
        const cards = (key: string) => grouped(key).map(problem => <Card key={problem.id} problem={problem} disabled={stopped || !online} onMaterial={material} onAction={(target, trigger) => { void act(target, problem, trigger); }} onDisclosure={open => { if (open)
            disclosed.add(problem.id);
        else {
            disclosed.delete(problem.id);
            if (problem.state === 'finished')
                render();
        } }}/>);
        const recent = grouped('recent');
        mountSurface(body, <><div className="status-empty" hidden={!online || stopped || shown.count !== 0}><div className="status-check">✓</div><h3>Всё в порядке</h3><p>Сейчас ничего не требует вашего внимания.</p></div>{sections.map(([key, label]) => <div key={key} className="status-group" hidden={!grouped(key).length && !(key === 'wait' && work)}><h3>{label}</h3><div>{cards(key)}</div>{key === 'wait' && <p className="status-work">{work}</p>}</div>)}<Recent count={recent.length}>{cards('recent')}</Recent><button id="status-journal-open" className="status-journal-button" onClick={journal}>Журнал диагностики</button></>);
        focusMode();
    }
    function open(trigger = toggle) { opener = trigger; panel.hidden = false; toggle.setAttribute('aria-expanded', 'true'); const current = actions.merge(snapshot).current.slice().sort((a, b) => b.created_at - a.created_at); const previous = new Map(owners); owners.clear(); for (const p of current)
        owners.set(p.id, previous.get(p.id) || p.section); for (const [id, owner] of previous)
        if (!owners.has(id))
            owners.set(id, owner); render(); focusMode(); byId('status-close').focus(); }
    byId('status-journal-back').onclick = () => { statePage(); byId('status-journal-open').focus(); };
    toggle.onclick = () => { if (panel.hidden) {
        statePage();
        open();
    }
    else
        close(); };
    byId('status-close').onclick = () => close();
    document.addEventListener('keydown', event => { if (panel.hidden || document.querySelector('dialog[open]'))
        return; if (event.key === 'Escape') {
        event.preventDefault();
        close();
    } if (event.key === 'Tab' && matchMedia('(max-width:700px)').matches) {
        const buttons = [...panel.querySelectorAll<HTMLElement>('button:not(:disabled),summary')].filter(n => n.getClientRects().length);
        const first = buttons[0], last = buttons.at(-1);
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
        }
        else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
        }
    } });
    window.addEventListener('resize', focusMode);
    const header = document.querySelector('header')!;
    new ResizeObserver(() => panel.style.setProperty('--reader-header-height', header.getBoundingClientRect().height + 'px')).observe(header);
    function prune(value: ProblemSnapshot) { const clean = (problem: Problem): Problem => { const objects = problem.objects.filter(o => o.kind !== 'material' || !deleted.has(o.id)); return objects.length !== problem.objects.length && !objects.length ? { ...problem, objects, action: null, state: 'finished', outcome: 'irrelevant' } : { ...problem, objects }; }; const current = value.current.map(clean).filter(p => p.state !== 'finished'); return { ...value, current, recent: value.recent.map(clean), count: current.length }; }
    render();
    return { open, redraw: render, forgetMaterials(ids: string[]) { actions.forgetMaterials(ids); ids.forEach(id => deleted.add(id)); snapshot = prune(snapshot); render(); }, discardDraft: (path: string, field: string, method?: string) => actions.discard(path, field, snapshot, method), update(value: ProblemSnapshot | undefined, state: Status) { online = true; snapshot = prune(value || emptySnapshot); work = state.stage === 'Готово' ? '' : `${state.stage} · источники ${state.ready_sources}/${state.total_sources}`; render(); }, unavailable() { online = false; render(); }, stop() { stopped = true; render(); }, refresh: onRefresh };
}
