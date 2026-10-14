// Ionicons folder (MIT); license: licenses/ionicons-LICENSE.txt.
const folderSvg = '<svg viewBox="0 0 512 512" fill="currentColor"><path d="M496 152a56 56 0 0 0-56-56H220.11a23.9 23.9 0 0 1-13.31-4L179 73.41A55.77 55.77 0 0 0 147.89 64H72a56 56 0 0 0-56 56v48a8 8 0 0 0 8 8h464a8 8 0 0 0 8-8ZM16 392a56 56 0 0 0 56 56h368a56 56 0 0 0 56-56V216a8 8 0 0 0-8-8H24a8 8 0 0 0-8 8Z"/></svg>';
export function treeButton(label, className) {
  const button = document.createElement('button');
  button.type = 'button'; button.className = className; button.title = label;
  button.setAttribute('aria-label', label);
  return button;
}
export function treeTitle(button, title, kind, count) {
  const icon = document.createElement('span');
  icon.className = 'source-icon ' + kind; icon.setAttribute('aria-hidden', 'true');
  if (kind === 'folder') icon.innerHTML = folderSvg;
  else if (kind === 'rss') icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="5" cy="19" r="1.5" fill="currentColor"/><path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/></svg>';
  else icon.textContent = kind === 'youtube' ? '▶' : kind === 'telegram' ? '➤' : '◷';
  const label = document.createElement('span'); label.className = 'tree-title'; label.textContent = title;
  button.append(icon, label);
  if (count !== undefined) {const value = document.createElement('span'); value.className = 'tree-count'; value.textContent = count || ''; button.append(value);}
}
export function treeChevron() {
  const arrow = document.createElement('span'); arrow.className = 'tree-chevron'; arrow.setAttribute('aria-hidden', 'true');
  arrow.innerHTML = '<svg viewBox="0 0 20 20"><path d="m7 4 6 6-6 6"/></svg>';
  return arrow;
}
export function folderFeeds(catalog, id) {
  return catalog.feeds.filter(feed => feed.folder_id === id).sort((a, b) => a.title.toLocaleLowerCase().localeCompare(b.title.toLocaleLowerCase()) || a.id.localeCompare(b.id));
}
export function feedIcon(feed) {return ['youtube', 'telegram'].includes(feed.kind) ? feed.kind : 'rss';}
