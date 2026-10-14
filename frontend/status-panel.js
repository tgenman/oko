// Owns presentation/focus of reader state, never source execution or settings drafts.
export function createStatusPanel({debug, actions, onSettings, onMaterial, onRetry, onRefresh}) {
  const byId = id => document.getElementById(id);
  const panel = byId('status-panel'), body = byId('status-body'), toggle = byId('status-toggle');
  const groups = new Map(), cards = new Map();
  const deleted = new Set();
  let snapshot = {current:[], recent:[], count:0}, online = null, stopped = false, opener = null;
  const connection = byId('status-connection'), connectionText = node('p'), checkConnection = node('button','Проверить связь');
  checkConnection.id = 'status-check-connection'; checkConnection.onclick = onRefresh; connection.append(connectionText,checkConnection);
  function node(tag, text = '', className = '') {
    const result = document.createElement(tag); result.textContent = text; result.className = className; return result;
  }
  const empty = node('div', '', 'status-empty');
  empty.append(node('div', '✓', 'status-check'), node('h3', 'Всё в порядке'), node('p', 'Сейчас ничего не требует вашего внимания.'));
  body.append(empty);
  for (const [key, label] of [['action','Нужно действие'], ['wait','Ожидание и работа'], ['object','Проблемы материалов и фидов']]) {
    const section = node('div', '', 'status-group'), heading = node('h3', label), list = node('div');
    section.append(heading, list); body.append(section); groups.set(key, {section, list});
  }
  const work = node('p', '', 'status-work'); groups.get('wait').section.append(work);
  const recent = node('details', '', 'status-recent'), recentTitle = node('summary'), recentList = node('div');
  recent.append(recentTitle, recentList); body.append(recent);
  const journalButton = node('button', 'Журнал диагностики', 'status-journal-button'); journalButton.id = 'status-journal-open';
  body.append(journalButton);
  byId('status-journal').append(byId('debug-panel'));
  function focusMode() {
    const modal = matchMedia('(max-width:700px)').matches;
    panel.setAttribute('role', modal ? 'dialog' : 'complementary');
    if (modal) panel.setAttribute('aria-modal', 'true'); else panel.removeAttribute('aria-modal');
    byId('reader-shell').inert = modal && !panel.hidden;
  }
  function open(trigger = toggle) {
    opener = trigger; panel.hidden = false; toggle.setAttribute('aria-expanded', 'true');
    snapshot.current.sort((a,b) => b.created_at - a.created_at); render();
    for (const problem of snapshot.current) {
      const entry = cards.get(problem.id);
      if (entry) groups.get(problem.section).list.append(entry.root);
    }
    focusMode(); byId('status-close').focus();
  }
  function close(restore = true) {
    panel.hidden = true; toggle.setAttribute('aria-expanded', 'false'); focusMode();
    debug.show(false);
    if (restore && opener?.isConnected) opener.focus({preventScroll:true});
  }
  function statePage() {
    body.hidden = false; byId('status-journal').hidden = true; byId('status-journal-back').hidden = true; debug.show(false);
  }
  journalButton.onclick = () => {
    body.hidden = true; byId('status-journal').hidden = false; byId('status-journal-back').hidden = false; debug.show(true);
    byId('status-journal-back').focus();
  };
  byId('status-journal-back').onclick = () => {statePage(); journalButton.focus();};
  toggle.onclick = () => {if (panel.hidden) {statePage(); open();} else close();};
  byId('status-close').onclick = () => close();
  document.addEventListener('keydown', event => {
    if (panel.hidden || document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape') {event.preventDefault(); close();}
    if (event.key === 'Tab' && matchMedia('(max-width:700px)').matches) {
      const buttons = [...panel.querySelectorAll('button:not(:disabled),summary')].filter(n=>n.getClientRects().length);
      const first = buttons[0], last = buttons.at(-1);
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
    }
  });
  window.addEventListener('resize', focusMode);
  const header = document.querySelector('header');
  new ResizeObserver(() => panel.style.setProperty('--reader-header-height', header.getBoundingClientRect().height + 'px')).observe(header);
  const actionNames = {settings:'Настроить модель', 'feed-settings':'Настройки фида', retry:'Повторить', 'theses-retry':'Повторить обработку', 'filter-retry':'Повторить фильтрацию', regenerate:'Пересоздать обзор', material:'Показать материал', objects:'Показать материалы', collect:'Обновить источники'};
  function makeCard(problem) {
    const root = node('div', '', 'status-problem'), icon = node('span', '!', 'status-icon'); icon.setAttribute('aria-hidden', 'true');
    const content = node('div', '', 'status-problem-content'), title = node('h3'), consequence = node('p'), next = node('p', '', 'status-next'), tag = node('small', '', 'status-tag');
    const action = node('button', '', 'status-action'), details = node('details', '', 'status-details'), summary = node('summary', 'Подробности'), reason = node('p'), objects = node('div');
    details.append(summary, reason, objects); content.append(tag, title, consequence, next, action, details); root.append(icon, content);
    const entry = {root, icon, title, consequence, next, tag, action, details, reason, objects, problem, objectButtons:new Map()};
    action.onclick = async () => {
      const target = entry.problem.action;
      if (!target) return;
      if (target.kind === 'settings' || target.kind === 'feed-settings' || target.kind === 'add') {
        // Keep the panel underneath the existing settings dialog so closing it returns here.
        byId('reader-shell').inert = false;
        await onSettings(target, action);
        const dialog = document.querySelector('dialog[open]');
        if (dialog) dialog.addEventListener('close', () => {focusMode(); if (!panel.hidden) byId('status-close').focus();}, {once:true});
      } else if (target.kind === 'objects') {details.open = true;}
      else if (target.kind === 'material') {close(false); onMaterial(target.id);}
      else try {await onRetry(target, entry.problem);} catch { /* The command outcome is already tracked by its API owner. */ }
    };
    details.addEventListener('toggle', () => {if (!details.open && entry.problem.state === 'finished') render();});
    return entry;
  }
  function updateCard(entry, problem) {
    entry.problem = problem;
    entry.root.dataset.problemId = problem.id; entry.root.dataset.section = problem.section;
    entry.root.classList.toggle('status-finished', problem.state === 'finished');
    entry.title.textContent = problem.title; entry.consequence.textContent = problem.consequence;
    entry.next.textContent = problem.next_step;
    entry.reason.textContent = problem.reason || ''; entry.reason.hidden = !problem.reason;
    if (problem.next_at) entry.next.textContent += ' · ' + new Date(problem.next_at * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    entry.icon.textContent = problem.state === 'finished' ? '✓' : problem.section === 'wait' ? '◷' : '!';
    entry.tag.textContent = problem.saved === false ? 'Не сохранено' : problem.state === 'finished' ? problem.outcome === 'irrelevant' ? 'Больше не актуально' : 'Устранено' : problem.state === 'retrying' ? 'Повторяем…' : '';
    entry.tag.hidden = !entry.tag.textContent;
    entry.action.hidden = !problem.action || problem.state === 'finished';
    entry.action.textContent = problem.action?.kind === 'settings' && problem.code !== 'codex-choice' ? 'Открыть настройки' : actionNames[problem.action?.kind] || 'Открыть настройки';
    entry.action.disabled = stopped || !online || problem.state === 'retrying';
    const wanted = new Set(problem.objects.map(object=>object.kind+':'+object.id));
    for (const [key, button] of entry.objectButtons) if (!wanted.has(key)) {button.remove();entry.objectButtons.delete(key);}
    for (const object of problem.objects) {
      const key = object.kind+':'+object.id;
      let button = entry.objectButtons.get(key);
      if (!button) {
        button = node('button', object.label, 'status-object');
        button.onclick = () => {if (object.kind === 'material') {close(false); onMaterial(object.id);} else if (entry.problem.action) entry.action.click();};
        entry.objectButtons.set(key, button); entry.objects.append(button);
      }
      button.textContent = object.label;
      button.disabled = stopped || !online || (object.kind !== 'material' && !problem.action);
    }
  }
  function render() {
    const shown = actions.merge(snapshot);
    toggle.textContent = stopped ? 'Око остановлено' : !online ? 'Нет связи с Око' : shown.count ? `Проблемы · ${shown.count}` : 'Состояние';
    toggle.classList.toggle('has-problems', shown.count > 0 || !online);
    byId('status-summary').textContent = stopped ? 'Око остановлено' : !online ? 'Последние известные сведения' : shown.count ? `Требуют внимания: ${shown.count}` : 'Проблем нет · можно читать';
    connection.hidden = Boolean(online) || stopped;
    connectionText.textContent = snapshot.updated_at ? 'Нет связи с Око · Состояние может быть устаревшим. Последнее обновление: ' + new Date(snapshot.updated_at*1000).toLocaleTimeString() : 'Состояние Око пока неизвестно. Ожидаем первого ответа.';
    byId('collect').disabled = stopped || !online;
    byId('stop').disabled = stopped || !online;
    const wanted = new Set([...shown.current,...shown.recent].map(p=>p.id));
    for (const [id, entry] of cards) if (!wanted.has(id)) {entry.root.remove(); cards.delete(id);}
    for (const problem of [...shown.current,...shown.recent]) {
      let entry = cards.get(problem.id);
      if (!entry) {entry = makeCard(problem); cards.set(problem.id, entry);}
      updateCard(entry, problem);
      const held = problem.state === 'finished' && entry.details.open && entry.root.closest('.status-group');
      const owner = held ? entry.root.parentNode : problem.state === 'finished' ? recentList : groups.get(problem.section).list;
      if (entry.root.parentNode !== owner) owner.append(entry.root);
    }
    for (const {section,list} of groups.values()) section.hidden = !list.children.length && !(section === groups.get('wait').section && work.textContent);
    empty.hidden = !online || stopped || shown.count !== 0;
    recentTitle.textContent = `Недавно завершённые · ${recentList.children.length}`; recent.hidden = !recentList.children.length;
    focusMode();
  }
  render();
  return {
    open,
    redraw: render,
    forgetMaterials(ids) {
      if (!ids.length) return;
      actions.forgetMaterials(ids);
      ids.forEach(id=>deleted.add(id));
      const prune = problem => {
        const before = problem.objects.length;
        problem.objects = problem.objects.filter(obj=>obj.kind!=='material'||!deleted.has(obj.id));
        if (before && !problem.objects.length) {problem.action=null;problem.state='finished';problem.outcome='irrelevant';}
      };
      snapshot.current.forEach(prune); snapshot.recent.forEach(prune);
      snapshot.current = snapshot.current.filter(p=>p.state!=='finished');snapshot.count=snapshot.current.length;render();
    },
    discardDraft: (path,field,method)=>actions.discard(path,field,snapshot,method),
    update(value, state) {
      online = true; snapshot = value || {current:[], recent:[], count:0};
      for (const p of [...snapshot.current,...snapshot.recent]) {
        if (p.objects.some(obj=>obj.kind==='material'&&deleted.has(obj.id))) {
          p.objects=p.objects.filter(obj=>obj.kind!=='material'||!deleted.has(obj.id));
          if (!p.objects.length) {p.action=null;p.state='finished';p.outcome='irrelevant';}
        }
      }
      snapshot.current=snapshot.current.filter(p=>p.state!=='finished');snapshot.count=snapshot.current.length;
      work.textContent = state.stage === 'Готово' ? '' : `${state.stage} · источники ${state.ready_sources}/${state.total_sources}`;
      render();
    },
    unavailable() {online = false; render();},
    stop() {stopped = true; render();},
    refresh: onRefresh,
  };
}
