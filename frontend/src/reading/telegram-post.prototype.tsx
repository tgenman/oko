/// <reference types="vite/client" />
// THROWAWAY. Three layouts on /?variant=A/B/C: whole-post web restriction vs audio vs video size limit.
// Public post 2806 checked 2026-10-21; neighbouring rows are illustrative. No API, persistence or mutations.
import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {ReaderShell} from '../shell';
import {initializeReaderShell,mountSurface} from '../renderer';
import {ExternalAttachments,ExternalMediaIcon} from './external-attachments';
import './telegram-post.prototype.css';
const cases={
 post:{title:'Пост доступен только в Telegram',source:'Техножрица',date:'12 октября 2026, 14:49',url:'https://t.me/tech_priestess/2806',label:'Пост недоступен для веб-просмотра',state:'post_web_unavailable · тип содержимого неизвестен · текста нет'},
 audio:{title:'Эмигранты 2022 года — почти пять спустя. Аудиоверсия разбора',source:'Медуза — LIVE',date:'21 октября 2026',url:'https://t.me/meduzalive/149475',label:'Аудио доступно в Telegram',state:'external_audio · аудиофайл подтверждён · текст сохранён'},
 video:{title:'Публикация с видео',source:'safin4',date:'Дата публикации',url:'https://t.me/safin4/5894',label:'Видео слишком большое для веб-просмотра',state:'external_video · size_limit подтверждён · текст сохранён'},
 error:{title:'Фоторепортаж: события дня',source:'Демонстрационный источник',date:'21 октября 2026',url:'#',label:'Не удалось загрузить фото',state:'download_failed · реальный сбой · доступен повтор'},
};
type Case=keyof typeof cases; type Variant='A'|'B'|'C';
const names={A:'Отдельный экран поста',B:'Пояснение в области чтения',C:'Карточка ссылки'};
function TelegramIcon(){return <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M21 3 3 10c-1 .4-1 1 .1 1.3l4.5 1.4L18 6.4 9.7 14.2l-.3 4.6 3-3 4.8 3.5c.8.5 1.3.2 1.5-.6L22 4c.2-1-.2-1.3-1-1Z"/></svg>;}
function OpenLink(){return <a className="post-open" href={cases.post.url} target="_blank" rel="noreferrer">Открыть в Telegram ↗</a>;}
function Explanation(){return <p>Telegram не предоставляет содержимое этого поста для веб-просмотра.</p>;}
function VariantA(){return <section className="post-empty"><div className="post-symbol"><TelegramIcon/></div><h2>Пост доступен только в Telegram</h2><Explanation/><OpenLink/></section>;}
function VariantB(){return <section className="post-inline"><span className="post-symbol"><TelegramIcon/></span><div><h2>Содержимое доступно в Telegram</h2><Explanation/><OpenLink/></div></section>;}
function VariantC(){return <section className="post-link-card"><div className="post-link-banner"><TelegramIcon/><span>TELEGRAM · ВЕБ-ПРОСМОТР НЕДОСТУПЕН</span></div><h2>Техножрица · пост №2806</h2><Explanation/><div className="post-link-footer"><span>t.me/tech_priestess/2806</span><OpenLink/></div></section>;}
function App(){
 const readCase=():Case=>{const v=new URLSearchParams(location.search).get('case');return v==='audio'||v==='video'||v==='error'?v:'post';};
 const readVariant=():Variant=>{const v=new URLSearchParams(location.search).get('variant');return v==='B'||v==='C'?v:'A';};
 const [kind,setKind]=useState<Case>(readCase),[variant,setVariant]=useState<Variant>(readVariant),[viewed,setViewed]=useState(false);
 function select(c:Case){setKind(c);setViewed(false);const u=new URL(location.href);u.searchParams.set('case',c);history.replaceState(null,'',u);}
 function cycle(delta:number){const values:Variant[]=['A','B','C'];const next=values[(values.indexOf(variant)+delta+3)%3]!;setVariant(next);const u=new URL(location.href);u.searchParams.set('variant',next);history.replaceState(null,'',u);}
 useEffect(()=>{const key=(e:KeyboardEvent)=>{if(e.target instanceof Element&&e.target.closest('input,textarea,select,[contenteditable]'))return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();cycle(e.key==='ArrowRight'?1:-1);}};const pop=()=>{setKind(readCase());setVariant(readVariant());};window.addEventListener('keydown',key);window.addEventListener('popstate',pop);return()=>{window.removeEventListener('keydown',key);window.removeEventListener('popstate',pop);};},[variant]);
 const row=cases[kind];
 return <>{createPortal(<div className="post-list"><header>Новости <small>4</small></header><div className="post-date">Октябрь 2026</div>{(Object.keys(cases) as Case[]).map(c=><button key={c} className={'post-row '+(kind===c?'selected':'')} onClick={()=>select(c)}><span className={'post-thumb thumb-'+c}>{c==='post'?<TelegramIcon/>:c==='error'?'▧':<ExternalMediaIcon kind={c}/>}<span className={'post-badge '+(c==='error'?'failure':'')} title={cases[c].label} aria-label={cases[c].label}>{c==='post'?<TelegramIcon/>:c==='error'?'!':<ExternalMediaIcon kind={c}/>}</span></span><span><strong>{cases[c].title}</strong><small>{cases[c].source}</small>{c==='error'&&<em>Не удалось загрузить фото</em>}</span></button>)}</div>,document.getElementById('material-list')!)}
 <article className={'post-material case-'+kind} data-case={kind} data-variant={variant}><div className="post-source">{row.source} · Telegram</div>{!(kind==='post'&&variant==='A')&&<h1>{kind==='post'&&variant==='C'?'Публикация в Telegram':row.title}</h1>}<div className="post-date-line">{row.date}</div>
 {kind==='post'?(variant==='A'?<VariantA/>:variant==='B'?<VariantB/>:<VariantC/>):kind==='error'?<><p>Демонстрационный текст публикации сохранён.</p><div className="post-error"><strong>Не удалось загрузить фото</strong><p>Источник не ответил. Попробуйте загрузить файл ещё раз.</p><button onClick={e=>{e.currentTarget.textContent='Повтор запрошен · демонстрация';}}>Повторить загрузку</button></div></>:<><ExternalAttachments attachments={[{kind,source_url:row.url,reason:'public_preview_unavailable',...(kind==='video'?{restriction:'size_limit' as const}:{title:'Аудиоверсия разбора',author:'Радио Медуза'})}]} poster={()=>null}/><p className="post-demo-text">Демонстрационный текст публикации. При ограничении отдельного вложения текст остаётся доступен здесь.</p></>}
 <footer className="post-footer"><a href={row.url} target="_blank" rel="noreferrer">Исходная публикация ↗</a><button onClick={()=>setViewed(!viewed)}>{viewed?'Просмотрено ✓':'Просмотрено'}</button></footer></article>
 {import.meta.env.DEV&&createPortal(<aside className="post-switch"><div><button aria-label="Предыдущий вариант" onClick={()=>cycle(-1)}>←</button><strong>{variant} · {names[variant]}</strong><button aria-label="Следующий вариант" onClick={()=>cycle(1)}>→</button></div><small>ПРОТОТИП · {row.state} · ошибок {kind==='error'?1:0} · ИИ {kind==='post'?'не запускается':'по доступному тексту'}</small></aside>,document.body)}</>;
}
if(import.meta.env.DEV){initializeReaderShell(<ReaderShell/>);for(const b of document.querySelectorAll<HTMLButtonElement>('button'))b.disabled=true;document.body.classList.add('post-prototype');document.getElementById('reading-nav')!.innerHTML='<h2>Чтение</h2><p>▾ Новости　4</p><p>　Техножрица</p><p>　Медуза — LIVE</p><p>　safin4</p>';document.getElementById('progress')!.textContent='Прототип · три разных ограничения и один реальный сбой';document.getElementById('status-toggle')!.textContent='Problems · 1';mountSurface(document.getElementById('material')!,<App/>);}
