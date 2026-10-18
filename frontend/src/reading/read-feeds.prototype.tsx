// THROWAWAY PROTOTYPE: three ways to show/hide completed feeds, on /?variant=A|B|C.
// Synthetic data only. No API, account, storage or mutations outside this page.
import {useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createPortal} from 'react-dom';
import {ReaderShell} from '../shell';
import './read-feeds.prototype.css';

type Feed={id:string;title:string;folder:string;unread:number;hidden:number;color:string};
const initial:Feed[]=[
 {id:'ai',title:'Новости AI',folder:'Технологии',unread:4,hidden:0,color:'#4c75cf'},
 {id:'ml',title:'Практика ML',folder:'Технологии',unread:0,hidden:0,color:'#af73bb'},
 {id:'arch',title:'Архитектура',folder:'Технологии',unread:2,hidden:0,color:'#d29355'},
 {id:'platform',title:'Платформа',folder:'Технологии',unread:3,hidden:0,color:'#609895'},
 {id:'design',title:'Дизайн каждый день',folder:'Личное',unread:0,hidden:0,color:'#dd8181'},
 {id:'books',title:'Книжная полка',folder:'Личное',unread:0,hidden:0,color:'#8c8b62'},
 {id:'oko',title:'Новости Око',folder:'Работа',unread:1,hidden:0,color:'#5a8dcc'},
 {id:'work',title:'Рабочая группа',folder:'Работа',unread:2,hidden:2,color:'#9b77b0'},
];
const variants=['A','B','C'] as const;type Variant=typeof variants[number];type Mode='unread'|'all'|'read';
const names={A:'Кнопка в заголовке',B:'Фильтры списка',C:'Отдельный раздел'};
const groupNames=['Технологии','Личное','Работа'];
function App(){
 const [ready,setReady]=useState(false),[variant,setVariant]=useState<Variant>(()=>{const q=new URLSearchParams(location.search).get('variant');return variants.find(v=>v===q)||'A';});
 const [feeds,setFeeds]=useState(initial),[mode,setMode]=useState<Mode>('unread'),[selected,setSelected]=useState('ai');
 const current=feeds.find(f=>f.id===selected)!,read=feeds.filter(f=>f.unread===0),active=feeds.filter(f=>f.unread>0),count=feeds.reduce((n,f)=>n+f.unread-f.hidden,0);
 const pinned=mode==='unread'&&current.unread===0;
 useEffect(()=>{document.getElementById('progress')!.textContent='Прототип · данные примера · меняется только список фидов';setReady(true);},[]);
 function change(next:Variant){const url=new URL(location.href);url.searchParams.set('variant',next);history.replaceState({},'',url);setVariant(next);setMode('unread');}
 useEffect(()=>{const key=(e:KeyboardEvent)=>{if((e.target as HTMLElement).closest('input,textarea,select,[contenteditable]'))return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();change(variants[(variants.indexOf(variant)+(e.key==='ArrowRight'?1:2))%3]);}};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);},[variant]);
 function row(feed:Feed){return <button key={feed.id} className={'prototype-feed '+(selected===feed.id?'selected ':'')+(feed.unread===0?'completed':'')} data-feed-id={feed.id} onClick={()=>setSelected(feed.id)}><span className="prototype-avatar" style={{background:feed.color}}>{feed.title.slice(0,1)}</span><span className="prototype-feed-text">{feed.title}{feed.hidden>0&&<small>{feed.hidden} скрыты фильтром</small>}{feed.unread===0&&<small>{selected===feed.id&&pinned?'Открытый фид · всё просмотрено':'Всё просмотрено'}</small>}</span>{feed.unread-feed.hidden>0?<span className="tree-count">{feed.unread-feed.hidden}</span>:feed.unread===0?<span className="prototype-done">✓</span>:null}</button>;}
 function groups(rows:Feed[]){return groupNames.map(folder=>{const children=rows.filter(f=>f.folder===folder);return children.length?<div className="prototype-group" key={folder}><div className="prototype-folder">▰ <span>{folder}</span><span>{children.reduce((n,f)=>n+f.unread-f.hidden,0)||''}</span></div>{children.map(row)}</div>:null;});}
 const shown=mode==='all'?feeds:mode==='read'?read:feeds.filter(f=>f.unread>0||f.id===selected);
 const sidebar=<>
  {variant==='A'&&<div className="prototype-heading"><h2>Фиды</h2><button className={'prototype-eye '+(mode==='all'?'on':'')} aria-pressed={mode==='all'} onClick={()=>setMode(mode==='all'?'unread':'all')}>{mode==='all'?'◉ Скрыть прочитанные':'◉ Показать прочитанные'} <b>{read.length}</b></button></div>}
  {variant==='B'&&<><h2>Фиды</h2><div className="prototype-tabs" aria-label="Фильтр фидов">{([['unread','С новыми',active.length],['all','Все',feeds.length],['read','Прочитанные',read.length]] as const).map(([value,title,n])=><button key={value} aria-pressed={mode===value} className={mode===value?'on':''} onClick={()=>setMode(value)}>{title}<b>{n}</b></button>)}</div></>}
  {variant==='C'&&<div className="prototype-heading"><h2>Фиды</h2><span className="prototype-note">{active.length} с материалами</span></div>}
  <button className="prototype-all" onClick={()=>setSelected('ai')}>▣ Все материалы <span>{count}</span></button>
  {groups(variant==='C'?feeds.filter(f=>f.unread>0||f.id===selected):shown)}
  {variant==='C'&&<div className="prototype-completed"><button className="prototype-tray" aria-expanded={mode==='all'} onClick={()=>setMode(mode==='all'?'unread':'all')}><span>{mode==='all'?'⌄':'›'} Прочитанные</span><b>{read.length}</b></button>{mode==='all'&&groups(read.filter(f=>f.id!==selected))}</div>}
  {mode==='read'&&read.length===0&&<p className="prototype-note">Пока нет прочитанных фидов</p>}
 </>;
 const cards=<><div className="list-heading"><div><h2>{current.title}</h2><p>{current.unread-current.hidden} материалов</p></div></div><div id="card-scroll"><div id="cards">{current.unread===0?<div className="prototype-empty"><span>✓</span><h3>Всё просмотрено</h3><p>Новые публикации появятся здесь.</p></div>:current.hidden===current.unread?<div className="prototype-empty"><h3>Материалы скрыты фильтром</h3><p>Этот фид не считается прочитанным.</p><button onClick={()=>setFeeds(feeds.map(f=>f.id===selected?{...f,hidden:0}:f))}>Показать скрытые</button></div>:Array.from({length:current.unread-current.hidden},(_,i)=><button key={i} className={'material-row '+(i===0?'active':'')}><span className="card-preview"><span>▥</span></span><span className="card-body"><span className="card-title">{['Как мы читаем и выбираем главное','Несколько заметок о новых инструментах','Практика и полезные ссылки','Что изменилось на этой неделе'][i%4]}</span><span className="card-snippet">Пример материала в привычном интерфейсе Око. Содержание помогает оценить плотность и расположение переключателя.</span><span className="card-meta">{current.title} · Сегодня</span></span></button>)}</div></div></>;
 const material=<div className="prototype-material">{current.unread>current.hidden?<><p className="meta">Полностью сохранено</p><h1>Как мы читаем и выбираем главное</h1><p className="meta">Сегодня · {current.title}</p><details open><summary>Оригинал публикации</summary><div id="text">Небольшой поток полезных материалов проще разбирать, когда рядом остаются только фиды с новыми публикациями.<br/><br/>Прочитанные каналы никуда не удаляются. Переключатель возвращает их в навигацию, когда нужно посмотреть настройки или найти знакомый источник.<br/><br/>В этом прототипе кнопка ниже завершает разбор текущего фида только в примере. Можно увидеть, что произойдёт с выбранным фидом после последнего материала.</div></details><div className="actions"><button id="viewed" onClick={()=>setFeeds(feeds.map(f=>f.id===selected?{...f,unread:0,hidden:0}:f))}>Просмотрено · завершить пример</button></div></>:<div className="prototype-empty"><span>✓</span><h1>{current.title}</h1><p>{current.unread===0?'Фид остаётся подключённым.':'Два материала ещё не разобраны.'}</p></div>}</div>;
 return <><ReaderShell/>{ready&&<>{createPortal(sidebar,document.getElementById('reading-nav')!)}{createPortal(cards,document.getElementById('material-list')!)}{createPortal(material,document.getElementById('material')!)}</>}
 <div className="prototype-switcher"><button aria-label="Предыдущий вариант" onClick={()=>change(variants[(variants.indexOf(variant)+2)%3])}>←</button><div><strong>{variant} · {names[variant]}</strong><small>UI-прототип · ← → для сравнения</small></div><button aria-label="Следующий вариант" onClick={()=>change(variants[(variants.indexOf(variant)+1)%3])}>→</button><button className="prototype-reset" onClick={()=>{setFeeds(initial);setSelected('ai');setMode('unread');}}>Сбросить</button><details><summary>Состояние</summary><pre>{JSON.stringify({variant,filter:mode,selected,pinned,feeds:feeds.map(f=>({id:f.id,unread:f.unread,hidden:f.hidden}))},null,2)}</pre></details></div>
 </>;
}
if(import.meta.env.DEV)createRoot(document.getElementById('react-root')!).render(<App/>);
