'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const tab = crypto.randomUUID();
const byId = id => document.getElementById(id);
const bus = new BroadcastChannel('oko-reader');
let selected = null;
let generation = 0;
let stopped = false;
let ids = new Set();
async function api(path, method = 'GET') {
  const response = await fetch('/api/' + path, {method, cache: 'no-store', headers: {'Authorization': 'Bearer ' + token}});
  if (!response.ok) throw new Error(response.status === 403 ? 'Откройте Око командой запуска' : response.status === 503 ? 'Хранилище недоступно; сбор остановлен' : 'Связь с Око потеряна');
  return response.json();
}
function clear() {
  generation++;
  selected = null;
  byId('material').hidden = true;
  byId('empty').hidden = false;
  ['title', 'text', 'date', 'message'].forEach(id => { byId(id).textContent = ''; });
}
function stopUi(message) {
  stopped = true;
  clear();
  byId('cards').replaceChildren();
  byId('folders').replaceChildren();
  byId('count').textContent = '0';
  byId('progress').textContent = message;
  byId('stop').disabled = true;
}
bus.onmessage = event => {
  if (event.data === 'stop') stopUi('Око остановлено');
  else if (selected && event.data === selected.id) clear();
};
async function open(id) {
  const stamp = ++generation;
  try {
    const material = await api('materials/' + id);
    if (stamp !== generation || !ids.has(id) || stopped) return;
    selected = material;
    byId('title').textContent = material.title;
    byId('text').textContent = material.text;
    byId('completeness').textContent = material.complete ? 'Тестовый источник · Полностью сохранено' : 'Загрузка материала';
    byId('date').textContent = new Date(material.published * 1000).toLocaleString('ru');
    byId('message').textContent = '';
    byId('material').hidden = false;
    byId('empty').hidden = true;
  } catch (error) { clear(); byId('progress').textContent = error.message; }
}
async function refresh() {
  if (stopped) return;
  try {
    const state = await api('status');
    if (state.stopped) { stopUi('Око остановлено'); return; }
    byId('progress').textContent = state.error || `${state.stage} · источники ${state.ready_sources}/${state.total_sources}` + (state.errors.length ? ' · ' + state.errors.join(', ') : '');
    if (state.error) { clear(); return; }
    const cards = await api('cards');
    ids = new Set(cards.map(card => card.id));
    if (selected && !ids.has(selected.id)) clear();
    if (selected && cards.some(card => card.id === selected.id && card.complete !== selected.complete)) await open(selected.id);
    byId('count').textContent = cards.length;
    byId('cards').replaceChildren(...cards.map(card => {
      const button = document.createElement('button');
      button.classList.toggle('active', selected?.id === card.id);
      button.textContent = card.title;
      const date = document.createElement('small');
      date.textContent = new Date(card.published * 1000).toLocaleString('ru');
      button.append(date);
      button.onclick = () => open(card.id);
      return button;
    }));
    const folders = await api('folders');
    byId('folders').replaceChildren(...folders.map(folder => {
      const button = document.createElement('button');
      button.textContent = `${folder.title} · ${folder.count}`;
      button.className = 'active';
      return button;
    }));
  } catch (error) { clear(); byId('progress').textContent = error.message; }
  if (!stopped) setTimeout(refresh, 500);
}
byId('copy').onclick = async () => {
  if (!selected) return;
  try {
    await navigator.clipboard.writeText(selected.url);
    byId('message').textContent = 'Ссылка скопирована';
  } catch { byId('message').textContent = 'Браузер не разрешил копирование'; }
};
byId('viewed').onclick = async () => {
  if (!selected) return;
  const id = selected.id;
  try {
    await api('materials/' + id + '/viewed', 'POST');
    bus.postMessage(id);
    clear();
  } catch (error) { byId('message').textContent = error.message; }
};
byId('stop').onclick = async () => {
  try { await api('stop', 'POST'); bus.postMessage('stop'); stopUi('Око остановлено'); }
  catch (error) { byId('progress').textContent = error.message; }
};
async function heartbeat() {
  if (stopped) return;
  try { await api('tabs/' + tab + '/heartbeat', 'POST'); }
  catch (error) { clear(); byId('progress').textContent = error.message; }
  if (!stopped) setTimeout(heartbeat, 2000);
}
window.addEventListener('pagehide', () => {
  fetch('/api/tabs/' + tab + '/close', {method:'POST', keepalive:true, headers:{'Authorization':'Bearer '+token}}).catch(() => {});
  clear();
});
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
heartbeat().then(refresh);
