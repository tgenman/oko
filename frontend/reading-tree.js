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
  function button(label, cls) {const b=document.createElement('button'); b.type='button'; b.className=cls; b.title=label; b.setAttribute('aria-label',label); return b;}
  function titleRow(b,title,icon,count) {
    const i=document.createElement('span'); i.className='source-icon ' + icon; i.setAttribute('aria-hidden','true');
    if(icon==='folder') i.innerHTML='<svg viewBox="0 0 24 20" width="22" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 17V4h7l2 3h11v10z" fill="#d0e4ed"/></svg>';
    else if(icon==='rss') i.innerHTML='<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="5" cy="19" r="1.5" fill="currentColor"/><path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/></svg>';
    else i.textContent=icon==='youtube'?'▶':icon==='telegram'?'➤':'◔';
    const t=document.createElement('span'); t.className='tree-title'; t.textContent=title;
    const c=document.createElement('span'); c.className='tree-count'; c.textContent=count || ''; b.append(i,t,c);
  }
  function render() {
    const next=JSON.stringify([catalog,sources,selection,collapsed]); if(next===signature) return; signature=next;
    const focus=document.activeElement?.dataset.treeKey, scroll=nav.scrollTop;
    all.replaceChildren(); titleRow(all,'Все материалы','all',catalog.count); all.classList.toggle('active',selection.kind==='all'); all.setAttribute('aria-pressed',String(selection.kind==='all'));
    const groups=[];
    for(const folder of catalog.folders) {
      const group=document.createElement('div'); group.className='reading-group';
      const row=document.createElement('div'); row.className='tree-row folder-row';
      const toggle=button((collapsed[folder.id]?'Раскрыть ':'Свернуть ') + folder.title,'tree-collapse'); toggle.dataset.collapse=folder.id; toggle.dataset.treeKey='collapse/'+folder.id;
      toggle.textContent=collapsed[folder.id]?'›':'⌄'; toggle.setAttribute('aria-expanded',String(!collapsed[folder.id]));
      const children=document.createElement('div'); children.id='reading-folder-'+folder.id; children.hidden=!!collapsed[folder.id]; toggle.setAttribute('aria-controls',children.id);
      toggle.onclick=()=>{collapsed[folder.id]=!collapsed[folder.id]; save(); render();};
      const b=button(folder.title,'tree-select'); b.dataset.folder=folder.id; b.dataset.treeKey='folder/'+folder.id; titleRow(b,folder.title,'folder',folder.count); b.onclick=()=>select('folder',folder.id);
      b.classList.toggle('active',selection.kind==='folder'&&selection.id===folder.id); b.setAttribute('aria-pressed',String(selection.kind==='folder'&&selection.id===folder.id)); row.append(toggle,b); group.append(row,children);
      for(const feed of catalog.feeds.filter(f=>f.folder_id===folder.id).sort((a,b)=>a.title.toLocaleLowerCase().localeCompare(b.title.toLocaleLowerCase()) || a.id.localeCompare(b.id))) {
        const fr=document.createElement('div'); fr.className='tree-row feed-row';
        const fb=button(feed.title,'tree-select'); fb.dataset.feed=feed.id; fb.dataset.treeKey='feed/'+feed.id;
        titleRow(fb,feed.title,['youtube','telegram'].includes(feed.kind)?feed.kind:'rss',feed.count);
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
