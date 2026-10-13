import {reportError} from './debug.js';
// Owns the complete lifetime of the selected card, including its sources.
export function createOpenedMaterial({api, fetchMedia, onViewed, onChanged}) {
  const byId = id => document.getElementById(id);
  let session = null;
  const active = current => session === current && !current.controller.signal.aborted;
  function element(tag, text = '') { const node = document.createElement(tag); node.textContent = text; return node; }
  function dispose() {
    if (!session) return;
    session.controller.abort();
    byId('material').querySelectorAll('video,audio').forEach(media => {
      media.pause(); media.removeAttribute('src'); media.load();
    });
    session.urls.forEach(url => URL.revokeObjectURL(url));
    byId('images').replaceChildren();
  }
  function clear(message = 'Выберите материал') {
    dispose();
    byId('versions').replaceChildren();
    byId('event-panel').hidden = true;
    byId('event-body').replaceChildren();
    byId('event-sources').replaceChildren();
    byId('filter-panel').hidden = true;
    ['filtered-text', 'filter-status'].forEach(id => { byId(id).textContent = ''; });
    byId('theses').replaceChildren();
    byId('theses-panel').hidden = true;
    session = null;
    byId('description-panel').hidden = true;
    byId('material').hidden = true;
    byId('empty').hidden = false;
    byId('empty').textContent = message;
    ['title', 'text', 'description', 'date', 'message', 'ai-status'].forEach(id => { byId(id).textContent = ''; });
  }
  async function open(card) {
    clear('Загрузка материала…');
    const current = session = {id: card.id, folder_id: card.folder_id, cardKey: JSON.stringify(card),
      members: new Set([card.id, ...(card.sources || []).map(source => source.id)]),
      controller: new AbortController(), urls: new Set(), material: null};
    const id = card.id;
    try {
      const material = await api('materials/' + id, 'GET', undefined, current.controller.signal);
      if (!active(current)) return;
      byId('event-panel').hidden = !(material.event || material.digest);
      byId('event-body').replaceChildren();
      byId('event-sources').replaceChildren();
      byId('original-panel').hidden = !!(material.event || material.digest);
      byId('copy').hidden = !!(material.event || material.digest);
      if (material.event || material.digest) renderEvent(material, current);
      current.material = material;
      for (const source of material.sources || []) current.members.add(source.id);
      byId('title').textContent = material.title;
      byId('text').textContent = material.text;
      byId('description').textContent = material.description || '';
      byId('description-panel').hidden = !material.description;
      const filter = material.filter;
      byId('filter-panel').hidden = !filter || filter.status === 'off';
      byId('filter-status').textContent = !filter || filter.status === 'off' ? '' : filter.status === 'done' ? (filter.restored ? 'Возвращено пользователем · ' : filter.hidden ? 'Скрыто · ' : filter.removed.length ? 'Рекламные вставки убраны · ' : 'Фильтрация завершена') + (filter.reason || '') : filter.error || 'Ожидание фильтрации';
      byId('filter-restore').hidden = !filter || filter.status !== 'done' || filter.restored || !(filter.hidden || filter.removed.length);
      byId('filter-retry').hidden = !filter?.error;
      byId('filtered-text').textContent = filter?.status === 'done' && !filter.hidden && !filter.restored && filter.removed.length ? material.display_text : '';

      byId('theses-panel').hidden = !material.ai && !material.theses;
      byId('ai-status').textContent = material.theses ? 'Тезисы готовы' : material.ai?.error || (material.ai?.status === 'running' ? 'Подготовка тезисов' : 'Ожидание обработчика');
      byId('theses-retry').hidden = !material.ai?.error;
      byId('theses').replaceChildren(...(material.theses?.points || []).map(point => {
        const li = element('li', point.text);
        for (const ref of point.sources) { const link = element('a', ' Источник'); link.href = ref.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; li.append(link); }
        return li;
      }));
      byId('original-panel').open = material.originals !== false && (!filter || filter.status !== 'done' || filter.restored || !filter.removed.length && !filter.hidden);
      renderVersions(material, byId('versions'), current);
      byId('completeness').textContent = !material.complete ? 'Загрузка материала' : material.loading ? (material.captions ? 'Получение субтитров и превью' : 'Текст сохранён · загрузка вложений') : material.captions ? (material.reason || 'Субтитры и превью сохранены') : material.full === false ? 'Неполный материал · ' + material.reason : 'Полностью сохранено';
      byId('retry').hidden = !material.complete || material.full !== false || material.loading;
      byId('completeness').textContent += (material.changed ? ' · Изменено' : '') + (material.source_deleted ? ' · Недоступно в источнике; локальная копия сохранена' : '');
      byId('date').textContent = material.published_known === false ? 'Дата неизвестна' : new Date(material.published * 1000).toLocaleString('ru');
      byId('message').textContent = '';
      byId('material').hidden = false;
      byId('empty').hidden = true;
      await appendMedia(material, byId('images'), current);
      if (material.event || material.digest) {
        for (const source of material.sources) {
          if (!active(current)) return;
          await appendMedia(source, byId('source-media-' + source.id), current);
        }
      }
    } catch (error) { reportError(error, 'opened-material.js'); if (active(current)) clear(error.message); }
  }
  function renderVersions(material, container, current) {
    container.replaceChildren();
    for (const previous of material.previous || []) {
      const details = element('details');
      details.append(element('summary', 'Предыдущая версия'));
      const body = element('div'); details.append(body);
      let loaded = false;
      details.ontoggle = async () => {
        if (!details.open || loaded) return;
        loaded = true;
        try {
          const version = await api(previous.url.slice('/api/'.length), 'GET', undefined, current.controller.signal);
          if (!active(current) || !details.isConnected) return;
          body.append(element('h3', version.title));
          if(version.description) body.append(element('p',version.description));
          body.append(element('p', version.text));
          await appendMedia(version, body, current);
        } catch (error) { reportError(error, 'opened-material.js');
          if (active(current) && details.isConnected) { body.textContent = error.message; loaded = false; }
        }
      };
      container.append(details);
    }
  }

  async function appendMedia(material, container, current) {
      for (const path of material.images || []) {
        if (!active(current)) return;
        const response = await fetchMedia(path, current.controller.signal);
        if (!response.ok) continue;
        const blob = await response.blob();
        if (!active(current)) return;
        const url = URL.createObjectURL(blob); current.urls.add(url);
        const img = document.createElement('img'); img.src = url; img.alt = 'Изображение материала'; container.append(img);
      }
      for (const attachment of material.attachments || []) {
        if (!active(current)) return;
        const response = await fetchMedia(attachment.url, current.controller.signal);
        if (!response.ok) continue;
        const blob = await response.blob();
        if (!active(current)) return;
        const url = URL.createObjectURL(blob); current.urls.add(url);
        let node;
        if (attachment.kind === 'image') { node = element('img'); node.alt = 'Изображение публикации'; }
        else if (attachment.kind === 'video' || attachment.kind === 'circle' || attachment.kind === 'audio') {
          node = element(attachment.kind === 'audio' ? 'audio' : 'video'); node.controls = true; node.preload = 'metadata';
          node.setAttribute('aria-label', attachment.kind === 'circle' ? 'Кружочек Telegram' : attachment.kind === 'audio' ? 'Аудио публикации' : 'Видео публикации');
          if (attachment.kind === 'circle') node.classList.add('circle');
        } else { node = element('a', 'Скачать вложение'); node.href = url; node.download = attachment.url.split('/').pop(); }
        if (node.tagName !== 'A') node.src = url;
        container.append(node);
      }
  }

  function renderEvent(material, current) {
    const body = byId('event-body');
    for (const [field, title] of [['points', 'Сводка'], ['updates', 'Обновление'], ['conflicts', 'Противоречия источников']]) {
      const points = (material.event || material.digest)[field] || [];
      if (!points.length) continue;
      body.append(element('h2', title));
      const list = element('ul');
      for (const point of points) {
        const li = element('li', point.text);
        for (const ref of point.sources) {
          const source = material.sources.find(s => s.id === ref.id);
          const link = element('a', ' · ' + (source?.title || 'Источник'));
          link.href = ref.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; li.append(link);
        }
        list.append(li);
      }
      body.append(list);
    }
    for (const source of material.sources) {
      const section = element('section');
      const link = element('a', source.title); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      const copy = element('button', 'Скопировать ссылку');
      copy.onclick = async () => {
        try { await navigator.clipboard.writeText(source.url); if (active(current)) byId('message').textContent = 'Ссылка скопирована'; }
        catch (error) { reportError(error, 'opened-material.js'); if (active(current)) byId('message').textContent = 'Браузер не разрешил копирование'; }
      };
      const details = element('details');
      const text = element('p', source.text); text.className = 'source-text';
      details.append(element('summary', 'Оригинал · ' + (source.published_known === false ? 'Дата неизвестна' : new Date(source.published * 1000).toLocaleString('ru'))));
      if(source.description) details.append(element('p',source.description));
      details.append(text);
      const media = element('div'); media.className = 'source-media'; media.id = 'source-media-' + source.id; details.append(media);
      const versions = element('div'); renderVersions(source, versions, current);
      section.append(link, copy, source.changed ? element('p', 'Изменено') : element('span'), details, versions); byId('event-sources').append(section);
    }
  }


  async function action(path, changed = false) {
    const current = session;
    if (!current?.material) return;
    try {
      await api('materials/' + current.id + path, 'POST');
      if (active(current)) onChanged(changed);
    } catch (error) { reportError(error, 'opened-material.js'); if (active(current)) byId('message').textContent = error.message; }
  }
  byId('retry').onclick = () => action('/retry');
  byId('theses-retry').onclick = () => action('/theses/retry');
  byId('filter-restore').onclick = () => action('/filter/restore', true);
  byId('filter-retry').onclick = () => action('/filter/retry');
  byId('copy').onclick = async () => {
    const current = session;
    if (!current?.material) return;
    try {
      await navigator.clipboard.writeText(current.material.url);
      if (active(current)) byId('message').textContent = 'Ссылка скопирована';
    } catch (error) { reportError(error, 'opened-material.js'); if (active(current)) byId('message').textContent = 'Браузер не разрешил копирование'; }
  };
  byId('viewed').onclick = async () => {
    const current = session;
    if (!current?.material) return;
    try {
      await api('materials/' + current.id + '/viewed', 'POST');
      if (active(current)) clear();
      onViewed(current.id);
    } catch (error) { reportError(error, 'opened-material.js'); if (active(current)) byId('message').textContent = error.message; }
  };
  return {
    get selection() { return session && {id: session.id, folder_id: session.folder_id}; },
    open, clear,
    reconcile(cards) {
      if (!session) return;
      const card = cards.find(card => card.id === session.id);
      if (!card) clear();
      else if (JSON.stringify(card) !== session.cardKey) void open(card);
    },
    viewed(identity) { if (session?.members.has(identity)) clear(); },
    folderCompleted(folder) { if (session?.folder_id === folder) clear(); },
  };
}
