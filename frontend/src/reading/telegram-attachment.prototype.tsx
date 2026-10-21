/// <reference types="vite/client" />
// THROWAWAY: three attachment notices on the existing / reader route, ?variant=A/B/C.
// Question: how to show audio unavailable through public Telegram without an error?
// Static public post 149475, fetched 2026-10-21. No API, accounts, persistence or mutations.
import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {mountVideoPrototype} from './telegram-video.prototype';
import {ReaderShell} from '../shell';
import {initializeReaderShell,mountSurface} from '../renderer';
import './telegram-attachment.prototype.css';
const post='https://t.me/meduzalive/149475';
const title='Эмигранты 2022 года — почти пять спустя. Почему одни вернулись в РФ, а другие остались за границей? Аудиоверсия разбора';
const names={A:'Пометка после текста',B:'Карточка аудиовложения',C:'Пометка под заголовком'};
type Variant=keyof typeof names;
const listMode=new URLSearchParams(location.search).has('listVariant');
const variantParam=listMode?'listVariant':'variant';
const listNames={A:'Значок на превью',B:'Значок справа от заголовка',C:'Строка под заголовком'};
function TelegramLink({label='Открыть в Telegram'}:{label?:string}){return <a className="tg-external" href={post} target="_blank" rel="noopener noreferrer">{label} <span aria-hidden="true">↗</span></a>;}
function PostText(){return <div className="tg-post-text"><p>Что произошло с эмигрантами 2022 года? Сколько из них вернулись? Почему уехавшие не стали полноценной диаспорой? И много ли россиян хотят эмигрировать сегодня? Подводим итоги опроса читателей «Медузы», который провели независимые социологи. Прочитать текст можно <a href="https://bit.ly/meduzamirror#/feature/2026/10/21/ostatsya-uehat-vernutsya-kak-zhizn-nashih-chitateley-posle-2022-goda-opredelili-tri-eti-strategii?utm_source=tg_live&amp;utm_medium=post_ru" target="_blank" rel="noopener noreferrer">здесь</a>.</p><p>«Радио Медуза» есть <a href="https://lnkfi.re/radiomeduza" target="_blank" rel="noopener noreferrer">на подкаст-платформах</a>, <a href="https://www.youtube.com/playlist?list=PLFfHPVoMQkB6hd-6N8tydtF9HKZIeCk8p" target="_blank" rel="noopener noreferrer">на ютубе</a> и <a href="https://onelink.to/tg_live_intext" target="_blank" rel="noopener noreferrer">в нашем приложении</a>.</p><a className="tg-signature" href="https://t.me/meduzalive" target="_blank" rel="noopener noreferrer">@meduzalive</a></div>;}
function VariantA(){return <><PostText/><div className="tg-inline-note"><span aria-hidden="true">♫</span><div><strong>Аудио доступно в Telegram</strong><p>Текст поста сохранён. Публичный просмотр Telegram не отдаёт аудиофайл.</p><TelegramLink label="Слушать в Telegram"/></div></div></>;}
function VariantB(){return <><div className="tg-audio-file"><div className="tg-audio-icon" aria-hidden="true">♫</div><div className="tg-file-copy"><span className="tg-eyebrow">Аудио · в Telegram</span><h2>Остаться, уехать или вернуться?</h2><p>Радио Медуза</p><TelegramLink label="Слушать в Telegram"/></div></div><p className="tg-file-caption">Аудиофайл недоступен через публичный просмотр Telegram. Текст поста сохранён ниже.</p><PostText/></>;}
function VariantC(){return <><div className="tg-header-note"><strong>Текст сохранён · аудио в Telegram</strong><p>Аудиофайл недоступен через публичный просмотр.</p><TelegramLink label="Слушать аудио в Telegram"/></div><PostText/></>;}

function attachmentLabel(kind?:string){return kind==='audio'?'Аудио доступно в Telegram':kind==='video'?'Видео доступно в Telegram':kind==='document'?'Файл доступен в Telegram':'Вложение доступно в Telegram';}
function ExternalIcon({kind}:{kind?:string}){return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{kind==='audio'?<><path d="M9 17V6l11-3v11M9 9l11-3"/><ellipse cx="6" cy="18" rx="3" ry="2.5"/><ellipse cx="17" cy="15" rx="3" ry="2.5"/></>:kind==='video'?<><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></>:kind==='document'?<><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8zM14 3v5h5M8 12h8M8 16h6"/></>:<><path d="M14 4h6v6M20 4l-9 9"/><path d="M10 5H5a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1v-5"/></>}</svg>;}
function ExternalMark({label=false,kind}:{label?:boolean;kind?:string}){const text=attachmentLabel(kind);return <span className={'tg-list-external'+(label?' labelled':'')} tabIndex={0} role="img" aria-label={text} title={text}><ExternalIcon kind={kind}/>{label&&<span>{text}</span>}<span className="tg-icon-tooltip" role="tooltip">{text}</span></span>;}
function PrototypeList({variant}:{variant:Variant}){
 const[scope,setScope]=useState<'feed'|'folder'>('folder');
 const rows=[{id:'audio',title,source:'Медуза — LIVE',date:'21 октября 2026',kind:'external',media:'audio',thumb:'♫'},
 {id:'regular',title:'Итоги дня: главные события и фотографии',source:'Медуза — LIVE',date:'21 октября 2026',kind:'normal',thumb:'▧'},
 {id:'document',title:'Материалы исследования: таблицы и приложения',source:'Исследования',date:'20 октября 2026',kind:'external',media:'document',thumb:'▤'},
 {id:'failed',title:'Фоторепортаж: что происходит в городе',source:'Медуза — LIVE',date:'20 октября 2026',kind:'error',thumb:'▧'},
 {id:'text',title:'Короткие заметки недели',source:'Другой источник',date:'19 октября 2026',kind:'normal',thumb:'≡'}];
 const shown=rows.filter(row=>scope==='folder'||row.source==='Медуза — LIVE');
 return <div className={'tg-list-demo list-variant-'+variant}><div className="tg-list-scope" aria-label="Контекст списка"><button aria-pressed={scope==='folder'} onClick={()=>setScope('folder')}>Папка</button><button aria-pressed={scope==='feed'} onClick={()=>setScope('feed')}>Фид</button></div><div className="tg-list-heading"><strong>{scope==='folder'?'Новости':'Медуза — LIVE'}</strong><span>{shown.length}</span><span className="tg-list-menu" aria-hidden="true">•••</span></div><div role="list">{shown.map((row,index)=><div key={row.id} role="listitem">{(!index||shown[index-1]?.date!==row.date)&&<div className="tg-list-date">{row.date}</div>}<div className={'material-row tg-demo-row '+(row.id==='audio'?'active':'')}><span className={'card-preview tg-demo-thumb thumb-'+row.id} ><span aria-hidden="true">{row.thumb}</span>{row.kind==='external'&&variant==='A'&&<span className="tg-overlay-arrow"><ExternalMark kind={row.media}/></span>}</span><span className="card-body"><span className="tg-demo-title-row"><span className="card-title">{row.title}</span>{row.kind==='external'&&variant==='B'&&<ExternalMark kind={row.media}/>}</span>{row.kind==='external'&&variant==='C'&&<ExternalMark label kind={row.media}/>}{row.kind==='error'&&<span className="tg-demo-error">! Не удалось загрузить фото</span>}{scope==='folder'&&<span className="card-meta"><span className="card-source">{row.source}</span></span>}</span></div></div>)}</div><p className="tg-list-state">ПРОТОТИП · {scope==='folder'?'папка':'фид'} · внешние вложения — пометка · сбой фото — ошибка</p></div>;
}

function PrototypeSwitcher({variant,cycle}:{variant:Variant;cycle(delta:number):void}){return <aside className="tg-switcher" aria-label="Варианты прототипа"><div><button aria-label="Предыдущий вариант" onClick={()=>cycle(-1)}>←</button><strong>{variant} · {(listMode?listNames:names)[variant]}</strong><button aria-label="Следующий вариант" onClick={()=>cycle(1)}>→</button></div><small>{listMode?'ПРОТОТИП СПИСКА · внешний файл ≠ сбой · справа выбранный вариант B':'ПРОТОТИП · текст сохранён · аудио снаружи · ошибок 0 · повтор загрузки не нужен'}</small></aside>;}
function Preview(){
 const read=():Variant=>{const v=new URLSearchParams(location.search).get(variantParam);return v==='B'||v==='C'?v:'A';};
 const[variant,setVariant]=useState<Variant>(read);
 function cycle(delta:number){const variants:Variant[]=['A','B','C'];const next=variants[(variants.indexOf(variant)+delta+3)%3]!;const url=new URL(location.href);url.searchParams.set(variantParam,next);history.replaceState(null,'',url);setVariant(next);}
 useEffect(()=>{const key=(event:KeyboardEvent)=>{if(event.altKey||event.ctrlKey||event.metaKey||event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable]'))return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();cycle(event.key==='ArrowRight'?1:-1);}};const pop=()=>setVariant(read());window.addEventListener('keydown',key);window.addEventListener('popstate',pop);return()=>{window.removeEventListener('keydown',key);window.removeEventListener('popstate',pop);};},[variant]);
 return <><div className="tg-prototype-material" data-variant={variant}><p className="meta">Медуза — LIVE · Telegram</p><h1>{title}</h1><p className="tg-post-date"><time dateTime="2026-10-21T11:13:01Z">21 октября 2026, 14:13</time></p><div key={variant}>{listMode?<VariantB/>:variant==='A'?<VariantA/>:variant==='B'?<VariantB/>:<VariantC/>}</div><footer className="tg-material-footer"><a href={post} target="_blank" rel="noopener noreferrer">Оригинал публикации ↗</a><button disabled>Просмотрено</button></footer></div>{listMode&&createPortal(<PrototypeList variant={variant}/>,document.getElementById('material-list')!)}{listMode?createPortal(<PrototypeSwitcher variant={variant} cycle={cycle}/>,document.body):<PrototypeSwitcher variant={variant} cycle={cycle}/>}</>;
}
const videoCase=new URLSearchParams(location.search).get('case')==='video';
if(import.meta.env.DEV&&videoCase)mountVideoPrototype();
if(import.meta.env.DEV&&!videoCase){
 initializeReaderShell(<ReaderShell/>);
 for(const button of document.querySelectorAll<HTMLButtonElement>('button'))button.disabled=true;
 document.getElementById('progress')!.textContent='Прототип · текст сохранён · аудио доступно в Telegram';
 document.getElementById('status-toggle')!.textContent='Status';
 document.getElementById('reading-nav')!.innerHTML='<h2>Чтение</h2><p class="tg-folder">▾ Новости <span>3</span></p><p>　Медуза — LIVE</p><p>　Другие источники</p>';
 document.getElementById('material-list')!.innerHTML='<h2>Новости</h2><div class="tg-list-card selected"><small>Медуза — LIVE · 21 октября, 14:13</small><h3></h3><span>Аудио в Telegram ↗</span></div><div class="tg-list-card"><small>Другие источники</small><h3>Следующая публикация</h3></div>';
 document.querySelector('.tg-list-card h3')!.textContent=title;
 if(listMode){document.body.classList.add('tg-list-prototype');document.getElementById('material-list')!.replaceChildren();document.getElementById('progress')!.textContent='Прототип списка · справа выбранная карточка вложения B · показан сбой фото';document.getElementById('status-toggle')!.textContent='Problems · 1';document.getElementById('status-toggle')!.classList.add('has-problems');document.querySelector('.tg-folder span')!.textContent='5';}
 mountSurface(document.getElementById('material')!,<Preview/>);
}
