/// <reference types="vite/client" />
// THROWAWAY: three ways to show Telegram's explicit "Media is too big" limitation.
// Existing reader /?case=video&variant=A/B/C. Public metadata, abbreviated mock text.
// No reader API, accounts, persistence or production behavior changes.
import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {ReaderShell} from '../shell';
import {initializeReaderShell,mountSurface} from '../renderer';
import data from './telegram-video.prototype.json';
import './telegram-attachment.prototype.css';
import './telegram-video.prototype.css';
type Variant='A'|'B'|'C';
const names={A:'Обложка со ссылкой',B:'Компактная карточка',C:'Пометка после текста'};
function VideoIcon(){return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></svg>;}
function Poster({className=''}:{className?:string}){return <img className={'tv-poster '+className} src={data.poster} referrerPolicy="no-referrer" alt="Обложка видео из публичного предпросмотра Telegram"/>;}
function SourceLink({label='Смотреть в Telegram'}:{label?:string}){return <a className="tv-link" href={data.post} target="_blank" rel="noopener noreferrer">{label} ↗</a>;}
function Text(){return <div className="tg-post-text tv-text"><p>В посте — размышления о перфекционизме, самокритике и способности переживать неудачи. Автор описывает, как стремление к идеалу мешает принимать обычную жизнь и пробовать снова.</p><p>В публичном просмотре доступен текст публикации; видео предлагается открыть в Telegram.</p><p className="tv-excerpt-label">Для макета показано краткое описание вместо полного текста поста.</p></div>;}
function VariantA(){return <><div className="tv-poster-card"><a className="tv-poster-link" href={data.post} target="_blank" rel="noopener noreferrer" aria-label="Смотреть видео в Telegram"><Poster/><span className="tv-duration">{data.duration}</span><span className="tv-poster-cta">Смотреть в Telegram ↗</span></a><div className="tv-poster-copy"><strong>Видео доступно в Telegram</strong><p>Telegram не показывает видео такого размера в публичном просмотре.</p></div></div><Text/></>;}
function VariantB(){return <><div className="tv-compact"><Poster/><div><span className="tg-eyebrow">Видео · {data.duration} · в Telegram</span><h2>Видео слишком большое для веб-просмотра</h2><SourceLink/></div></div><Text/></>;}
function VariantC(){return <><Text/><div className="tv-footnote"><VideoIcon/><div><strong>Видео · {data.duration}</strong><p>Доступно в Telegram: публичный просмотр ограничен размером видео.</p><SourceLink/></div></div></>;}
function List(){return <div className="tv-list"><div className="tg-list-heading"><strong>Чтение</strong><span>4</span></div><div className="tg-list-date">21 октября 2026</div><div className="material-row tg-demo-row active"><span className="card-preview tg-demo-thumb tv-list-thumb"><Poster/><span className="tg-overlay-arrow" role="img" aria-label="Видео доступно в Telegram" title="Видео доступно в Telegram"><VideoIcon/></span></span><span className="card-body"><span className="card-title">{data.title}</span><span className="card-meta">Альберт Сафин · 08:14</span></span></div><div className="material-row tg-demo-row"><span className="card-preview tg-demo-thumb"><span aria-hidden="true">♫</span><span className="tg-overlay-arrow" role="img" aria-label="Аудио доступно в Telegram" title="Аудио доступно в Telegram">♫</span></span><span className="card-body"><span className="card-title">Эмигранты 2022 года — почти пять спустя. Аудиоверсия разбора</span><span className="card-meta">Медуза — LIVE · 14:13</span></span></div><div className="material-row tg-demo-row"><span className="card-preview tg-demo-thumb" aria-hidden="true">≡</span><span className="card-body"><span className="card-title">Обычная публикация с сохранённым текстом</span><span className="card-meta">Синтетический пример</span></span></div><div className="material-row tg-demo-row"><span className="card-preview tg-demo-thumb" aria-hidden="true">▧</span><span className="card-body"><span className="card-title">Фоторепортаж</span><span className="tg-demo-error">! Истекло время загрузки фото</span><span className="card-meta">Синтетический пример настоящего сбоя</span></span></div><p className="tg-list-state">Внешние аудио и видео — пометки своих типов. Сетевой сбой фото остаётся ошибкой.</p></div>;}
function Preview(){
 const read=():Variant=>{const v=new URLSearchParams(location.search).get('variant');return v==='B'||v==='C'?v:'A';};
 const[variant,setVariant]=useState<Variant>(read),[mobileView,setMobileView]=useState<'material'|'list'>('material');
 function cycle(delta:number){const all:Variant[]=['A','B','C'];const next=all[(all.indexOf(variant)+delta+3)%3]!;const url=new URL(location.href);url.searchParams.set('variant',next);history.replaceState(null,'',url);setVariant(next);}
 useEffect(()=>{document.body.dataset.tvView=mobileView;},[mobileView]);
 useEffect(()=>{const key=(e:KeyboardEvent)=>{if(e.altKey||e.ctrlKey||e.metaKey||e.target instanceof Element&&e.target.closest('input,textarea,select,[contenteditable]'))return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();cycle(e.key==='ArrowRight'?1:-1);}};const pop=()=>setVariant(read());window.addEventListener('keydown',key);window.addEventListener('popstate',pop);return()=>{window.removeEventListener('keydown',key);window.removeEventListener('popstate',pop);};},[variant]);
 return <><div className="tg-prototype-material tv-material" data-variant={variant}><p className="meta">{data.source} · Telegram</p><h1>{data.title}</h1><p className="tg-post-date"><time dateTime={data.published}>21 октября 2026, 08:14</time></p>{variant==='A'?<VariantA/>:variant==='B'?<VariantB/>:<VariantC/>}<footer className="tg-material-footer"><SourceLink label="Оригинал публикации"/><button disabled>Просмотрено</button></footer></div>{createPortal(<List/>,document.getElementById('material-list')!)}{createPortal(<aside className="tg-switcher tv-switcher" aria-label="Варианты видео"><div className="tv-mobile-tabs"><button aria-pressed={mobileView==='list'} onClick={()=>setMobileView('list')}>Список</button><button aria-pressed={mobileView==='material'} onClick={()=>setMobileView('material')}>Материал</button></div><div><button aria-label="Предыдущий вариант" onClick={()=>cycle(-1)}>←</button><strong>{variant} · {names[variant]}</strong><button aria-label="Следующий вариант" onClick={()=>cycle(1)}>→</button></div><small>ПРОТОТИП · текст и обложка доступны · видео в Telegram · сбой фото остаётся ошибкой</small></aside>,document.body)}</>;
}
export function mountVideoPrototype(){
 initializeReaderShell(<ReaderShell/>);document.body.classList.add('tv-prototype');
 for(const button of document.querySelectorAll<HTMLButtonElement>('button'))button.disabled=true;
 document.getElementById('progress')!.textContent='Прототип · ограничение Telegram «Media is too big» · без повторов загрузки видео';
 document.getElementById('status-toggle')!.textContent='Problems · 1';document.getElementById('status-toggle')!.classList.add('has-problems');
 document.getElementById('reading-nav')!.innerHTML='<h2>Чтение</h2><p class="tg-folder">▾ Все публикации <span>4</span></p><p>　Альберт Сафин</p><p>　Медуза — LIVE</p>';
 document.getElementById('material-list')!.replaceChildren();
 mountSurface(document.getElementById('material')!,<Preview/>);
}
