import {reportError} from './debug.js';

// Owns list rows, scroll positions and local preview lifetimes, not opened content.
export function createMaterialList({onOpen, onRetry, fetchMedia}) {
  const byId = id => document.getElementById(id);
  const root = byId('cards'), scroll = byId('card-scroll'), panel = byId('material-list');
  const menu = byId('list-menu'), toggle = byId('list-menu-toggle'), divider = byId('list-resize');
  const rows = new Map(), positions = new Map();
  let area = 'all', cards = [], selected = null, restore = null, width = 440, signature = '';
  function element(tag, text = '', cls = '') {const node=document.createElement(tag); node.textContent=text; node.className=cls; return node;}
  function saved(key, fallback) {try {return localStorage.getItem(key) || fallback;} catch {return fallback;}}
  function save(key,value) {try {localStorage.setItem(key,String(value));} catch {}}
  byId('sort').value = ['newest','oldest','importance'].includes(saved('oko-material-sort','newest')) ? saved('oko-material-sort','newest') : 'newest';
  byId('sort').addEventListener('change',()=>save('oko-material-sort',byId('sort').value));
  const storedWidth = Number(saved('oko-material-width','440'));
  width = Number.isFinite(storedWidth) ? Math.max(320,Math.min(640,storedWidth)) : 440;
  function resize() {
    const main=panel.parentElement, nav=byId('reading-nav').getBoundingClientRect().width;
    const available=Math.max(320,main.clientWidth-nav-12-360), effective=Math.min(width,available);
    main.style.setProperty('--material-width',effective+'px');
    divider.setAttribute('aria-valuenow',String(Math.round(effective)));
    divider.setAttribute('aria-valuemax',String(Math.min(640,available)));
  }
  function setWidth(value) {width=Math.max(320,Math.min(640,value)); save('oko-material-width',width); resize();}
  resize(); new ResizeObserver(resize).observe(byId('reading-nav'));
  window.addEventListener('resize',resize);
  divider.onkeydown=event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault(); setWidth(Number(divider.getAttribute('aria-valuenow'))+(event.key==='ArrowRight'?10:-10));}};
  divider.onpointerdown=event=>{if(event.button!==0)return; divider.setPointerCapture(event.pointerId); const initial=Number(divider.getAttribute('aria-valuenow')), start=event.clientX; divider.onpointermove=move=>setWidth(initial+move.clientX-start);};
  divider.onpointerup=divider.onpointercancel=()=>{divider.onpointermove=null;};
  function closeMenu(returnFocus=false) {menu.hidden=true; toggle.setAttribute('aria-expanded','false'); if(returnFocus)toggle.focus();}
  toggle.onclick=()=>{menu.hidden=!menu.hidden; toggle.setAttribute('aria-expanded',String(!menu.hidden));};
  document.addEventListener('pointerdown',event=>{if(!menu.hidden&&!menu.contains(event.target)&&!toggle.contains(event.target))closeMenu();});
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'&&!menu.hidden){event.preventDefault(); closeMenu(true);}});
  byId('list-retry').onclick=onRetry;
  function anchor() {
    const top=scroll.getBoundingClientRect().top;
    const node=[...root.querySelectorAll('[data-card]')].find(node=>node.getBoundingClientRect().bottom>top);
    const entry=node&&rows.get(node.dataset.card);
    return {id:entry?.card.id, offset:entry?entry.node.getBoundingClientRect().top-top:0, top:scroll.scrollTop};
  }
  function applyAnchor(position) {
    if(!position)return;
    const entry=rows.get(position.id);
    if(entry)scroll.scrollTop+=entry.node.getBoundingClientRect().top-scroll.getBoundingClientRect().top-position.offset;
    else scroll.scrollTop=position.top;
  }
  function dispose(entry) {observer.unobserve(entry.node); entry.controller?.abort(); if(entry.url)URL.revokeObjectURL(entry.url);}
  function clearRows() {for(const entry of rows.values())dispose(entry); rows.clear(); cards=[]; signature=''; root.replaceChildren();}
  async function preview(entry) {
    if(!entry.path||entry.started)return;
    entry.started=true; const controller=entry.controller=new AbortController();
    const active=()=>rows.get(entry.card.id)===entry&&!controller.signal.aborted;
    try {
      const response=await fetchMedia(entry.path,controller.signal);
      if(!active())return;
      if(!response.ok)throw new Error('Не удалось загрузить превью: HTTP '+response.status);
      const blob=await response.blob(); if(!active())return;
      const url=entry.url=URL.createObjectURL(blob), img=element('img'); img.alt='';
      img.onerror=()=>{if(active()){entry.preview.replaceChildren(element('span',entry.symbol)); URL.revokeObjectURL(url); entry.url=null; reportError(new Error('Не удалось показать превью'),'Список материалов');}};
      img.src=url; entry.preview.replaceChildren(img);
    } catch(error) {if(error.name!=='AbortError')reportError(error,'Превью списка');}
  }
  const observer=new IntersectionObserver(entries=>{for(const observed of entries)if(observed.isIntersecting){const entry=rows.get(observed.target.dataset.card); if(entry)void preview(entry);}}, {root:scroll,rootMargin:'150px'});
  function date(card) {return card.published_known===false?'Дата неизвестна':new Date(card.published*1000).toLocaleString('ru',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
  function group(card) {
    if(card.published_known===false)return 'Дата неизвестна';
    const stamp=new Date(card.published*1000), today=new Date(), yesterday=new Date(); yesterday.setDate(today.getDate()-1);
    if(stamp.toDateString()===today.toDateString())return 'Сегодня';
    if(stamp.toDateString()===yesterday.toDateString())return 'Вчера';
    return stamp.toLocaleDateString('ru',{year:'numeric',month:'long',day:'numeric'});
  }
  function previewPath(card) {return card.digest||card.event?null:(card.images?.[0]||card.attachments?.find(a=>a.kind==='image')?.url);}
  function makeRow(card) {
    const node=element('button','','material-row'); node.type='button'; node.dataset.card=card.id;
    const entry={node,card,preview:element('span','','card-preview'),symbol:card.digest?'▤':card.event?'▧':'▥',path:previewPath(card),started:false,url:null,controller:null};
    entry.preview.setAttribute('aria-hidden','true'); entry.preview.append(element('span',entry.symbol));
    node.onclick=()=>{select(card.id); onOpen(entry.card);};
    node.onkeydown=event=>{if(!['ArrowUp','ArrowDown'].includes(event.key))return; event.preventDefault(); const index=cards.findIndex(c=>c.id===entry.card.id), next=cards[Math.max(0,Math.min(cards.length-1,index+(event.key==='ArrowDown'?1:-1)))]; if(next){rows.get(next.id)?.node.focus();}};
    return entry;
  }
  function content(entry,card) {
    entry.card=card;
    const body=element('span','','card-body'), heading=element('span',card.title,'card-title'); heading.title=card.title;
    body.append(heading);
    const kind=card.digest?'Обзор':card.event?'Событие':card.filter?.hidden?'Скрыто':'';
    const status=[kind,card.changed?'Изменено':'',card.loading||card.complete===false?'Загружается':''].filter(Boolean).join(' · ');
    if(status)body.append(element('span',status,'card-kind'));
    if(card.snippet)body.append(element('span',card.snippet,'card-snippet'));
    const meta=element('span','','card-meta'), sources=card.sources?.length?[...new Set(card.sources.map(s=>s.title||s.feed_id).filter(Boolean))].join(', '):(card.feed_title||card.feed_id||'');
    const source=element('span',sources,'card-source'); source.title=sources;
    meta.append(source,element('time',date(card),'card-date')); body.append(meta);
    entry.node.replaceChildren(entry.preview,body); entry.node.setAttribute('aria-label',(kind?kind+' · ':'')+card.title+' · '+sources+' · '+date(card));
  }
  function select(id) {selected=id; for(const [identity,entry] of rows){entry.node.classList.toggle('active',identity===id); entry.node.setAttribute('aria-pressed',String(identity===id));}}
  function count() {byId('count').textContent=String(cards.length); const n=cards.length, word=n%10===1&&n%100!==11?'карточка':n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?'карточки':'карточек'; byId('count-word').textContent=word;}
  function update(next,id) {
    const position=restore||anchor(), focused=document.activeElement?.dataset.card;
    restore=null;
    const nextSignature=JSON.stringify([next,byId('sort').value,new Date().toDateString()]);
    if(nextSignature===signature){select(id); byId('list-status').textContent=cards.length?'':'Нет материалов'; byId('list-retry').hidden=true; return;}
    signature=nextSignature;
    // Preserve stable row DOM and media across polling; replace only changed content.
    const ids=new Set(next.map(card=>card.id));
    for(const [identity,entry] of rows)if(!ids.has(identity)){dispose(entry); rows.delete(identity);}
    const nodes=[]; let previous=null;
    for(const card of next){
      const label=byId('sort').value==='importance'?null:group(card);
      if(label&&label!==previous){nodes.push(element('div',label,'date-group')); previous=label;}
      let entry=rows.get(card.id);
      const path=previewPath(card);
      if(entry&&(entry.path!==path||entry.card.version!==card.version)){dispose(entry); rows.delete(card.id); entry=null;}
      if(!entry){entry=makeRow(card); rows.set(card.id,entry); content(entry,card); observer.observe(entry.node);}
      else if(JSON.stringify(entry.card)!==JSON.stringify(card))content(entry,card);
      nodes.push(entry.node);
    }
    cards=next;
    if(nodes.length!==root.children.length||nodes.some((node,index)=>node!==root.children[index]))root.replaceChildren(...nodes);
    select(id); count(); byId('list-status').textContent=cards.length?'':'Нет материалов'; byId('list-retry').hidden=true;
    if(focused&&rows.has(focused))rows.get(focused).node.focus({preventScroll:true});
    applyAnchor(position);
  }
  function setArea(selection,catalog) {
    const key=selection.kind+'/'+(selection.id||'');
    if(key!==area){positions.set(area,anchor()); clearRows(); area=key; restore=positions.get(key)||{top:0}; scroll.scrollTop=0; selected=null; byId('list-status').textContent='Загрузка материалов…'; byId('list-retry').hidden=true; count(); closeMenu();}
    byId('list-title').textContent=selection.kind==='all'?'Все материалы':(catalog[selection.kind==='folder'?'folders':'feeds']||[]).find(item=>item.id===selection.id)?.title||'Материалы';
    const folder=selection.kind==='folder';
    for(const identity of ['compress','digest-now','folder-viewed']){byId(identity).hidden=!folder; byId(identity).disabled=!folder;}
  }
  function removed(identity) {
    const index=cards.findIndex(card=>card.id===identity||card.sources?.some(source=>source.id===identity));
    if(index<0)return;
    const next=cards.filter(card=>card.id!==identity&&!card.sources?.some(source=>source.id===identity));
    const affected=cards[index].id===selected||document.activeElement?.dataset.card===cards[index].id;
    update(next,affected?null:selected); const target=next[Math.min(index,next.length-1)];
    if(affected){if(target)rows.get(target.id).node.focus({preventScroll:true}); else toggle.focus({preventScroll:true});}
  }
  return {setArea,update,select,removed,
    error(message){byId('list-status').textContent='Не удалось обновить материалы: '+message; byId('list-retry').hidden=false;},
    clear(){clearRows(); selected=null; count(); byId('list-status').textContent=''; closeMenu();},
  };
}
