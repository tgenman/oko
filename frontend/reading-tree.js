import {treeButton as button, treeTitle as titleRow, treeChevron, folderFeeds, feedIcon} from './feed-tree-view.js';
export function createReadingTree({onSelect, onFeedSettings, onWarning}) {
  const nav = document.getElementById('reading-nav'), root = document.getElementById('folders');
  const all = document.getElementById('all-folders'), divider = document.getElementById('sidebar-resize');
  let selection = {kind:'all', id:null}, catalog = {folders:[], feeds:[]}, sources = [];
  let collapsed = {}, width = 280, signature = '';
  try {collapsed = JSON.parse(localStorage.getItem('oko-reading-collapsed') || '{}') || {}; width = Number(localStorage.getItem('oko-reading-width')) || 280;} catch {}
  if (typeof collapsed !== 'object' || Array.isArray(collapsed)) collapsed = {};
  const save = () => {try {localStorage.setItem('oko-reading-collapsed', JSON.stringify(collapsed)); localStorage.setItem('oko-reading-width', String(width));} catch {}};
  function resize(value) {
    width = Math.max(220, Math.min(480, Math.max(220, window.innerWidth - 700), value));
    document.querySelector('main').style.setProperty('--sidebar-width', width + 'px');
    divider.setAttribute('aria-valuenow', String(width)); save();
  }
  resize(width); window.addEventListener('resize', () => resize(width));
  divider.onkeydown = event => {if (['ArrowLeft','ArrowRight'].includes(event.key)) {event.preventDefault(); resize(width + (event.key === 'ArrowRight' ? 10 : -10));}};
  divider.onpointerdown = event => {if(event.button !== 0) return; divider.setPointerCapture(event.pointerId); divider.onpointermove = move => resize(move.clientX);};
  divider.onpointerup = divider.onpointercancel = () => {divider.onpointermove = null;};
  function select(kind, id, folder = null) {selection = {kind,id,folder}; signature=''; render(); onSelect(selection);}
  all.onclick = () => select('all',null);
  function render() {
    const next=JSON.stringify([catalog,sources,selection,collapsed]); if(next===signature) return; signature=next;
    const focus=document.activeElement?.dataset.treeKey, scroll=nav.scrollTop;
    all.replaceChildren(); titleRow(all,'Все материалы','all',catalog.count); all.classList.toggle('active',selection.kind==='all'); all.setAttribute('aria-pressed',String(selection.kind==='all'));
    const groups=[];
    for(const folder of catalog.folders) {
      const group=document.createElement('div'); group.className='reading-group';
      const row=document.createElement('div'); row.className='tree-row folder-row';
      const toggle=button((collapsed[folder.id]?'Раскрыть ':'Свернуть ') + folder.title,'tree-collapse'); toggle.dataset.collapse=folder.id; toggle.dataset.treeKey='collapse/'+folder.id;
      toggle.append(treeChevron()); toggle.setAttribute('aria-expanded',String(!collapsed[folder.id]));
      const children=document.createElement('div'); children.id='reading-folder-'+folder.id; children.hidden=!!collapsed[folder.id]; toggle.setAttribute('aria-controls',children.id);
      toggle.onclick=()=>{collapsed[folder.id]=!collapsed[folder.id]; save(); render();};
      const b=button(folder.title,'tree-select'); b.dataset.folder=folder.id; b.dataset.treeKey='folder/'+folder.id; titleRow(b,folder.title,'folder',folder.count); b.onclick=()=>select('folder',folder.id);
      b.classList.toggle('active',selection.kind==='folder'&&selection.id===folder.id); b.setAttribute('aria-pressed',String(selection.kind==='folder'&&selection.id===folder.id)); row.append(b,toggle); group.append(row,children);
      for(const feed of folderFeeds(catalog,folder.id)) {
        const fr=document.createElement('div'); fr.className='tree-row feed-row';
        const fb=button(feed.title,'tree-select'); fb.dataset.feed=feed.id; fb.dataset.treeKey='feed/'+feed.id;
        titleRow(fb,feed.title,feedIcon(feed),feed.count);
        fb.classList.toggle('active',selection.kind==='feed'&&selection.id===feed.id); fb.setAttribute('aria-pressed',String(selection.kind==='feed'&&selection.id===feed.id));
        fb.onclick=()=>select('feed',feed.id,folder.id); fb.ondblclick=()=>onFeedSettings(feed.id,fb); fr.append(fb);
        const source=sources.find(s=>s.feed_id===feed.id); const warning=[source?.error,source?.gap].filter(Boolean).join(' · ');
        if(warning) {const w=button(warning,'tree-warning'); w.textContent='⚠'; w.dataset.treeKey='warning/'+feed.id; w.onclick=onWarning; fr.append(w);}
        children.append(fr);
      }
      groups.push(group);
    }
    root.replaceChildren(...groups); nav.scrollTop=scroll;
    if(focus) [...nav.querySelectorAll('[data-tree-key]')].find(n=>n.dataset.treeKey===focus)?.focus({preventScroll:true});
  }
  return {
    get selection(){return selection;},
    update(data,status) {
      catalog=data; sources=status || [];
      let changed=false, moved=false;
      if(selection.kind==='feed') {
        const feed=catalog.feeds.find(f=>f.id===selection.id);
        if(!feed) {selection=catalog.folders.some(f=>f.id===selection.folder)?{kind:'folder',id:selection.folder}:{kind:'all',id:null}; changed=true;}
        else if(feed.folder_id!==selection.folder) {selection.folder=feed.folder_id; collapsed[feed.folder_id]=false; save(); moved=true;}
      } else if(selection.kind==='folder'&&!catalog.folders.some(f=>f.id===selection.id)) {selection={kind:'all',id:null}; changed=true;}
      render(); if(moved) [...root.querySelectorAll('[data-feed]')].find(n=>n.dataset.feed===selection.id)?.scrollIntoView({block:'nearest'});
      if(changed) onSelect(selection);
      return changed;
    },
    clear(){root.replaceChildren();},
  };
}
