/// <reference types="vite/client" />
// THROWAWAY: three reply-preview layouts on the existing /?variant=A/B/C route.
// Question: compact context before text, a disclosure, or context after text?
// Data is a static snapshot of the requested public post, extracted separately.
// This does not implement the Telegram parser or call the reader API.
import {useEffect,useState} from 'react';
import {ReaderShell} from '../shell';
import {initializeReaderShell,mountSurface} from '../renderer';
import data from './telegram-reply.prototype.json';
import './telegram-reply.prototype.css';

type Variant='A'|'B'|'C';
const names={A:'Превью как в Telegram',B:'Ответ под раскрытием',C:'Контекст после текста'};
const external={target:'_blank',rel:'noopener noreferrer'} as const;
function Body(){return <div className="tr-body">{data.text.split(/\n\s*\n/).map((paragraph,index)=><p key={index}>{paragraph.trim()}</p>)}</div>;}

export function VariantA(){
 const[expanded,setExpanded]=useState(false);
 return <><div className={'tr-reply-preview'+(expanded?' expanded':'')}><a href={data.reply.source_url} {...external} className="tr-reply-card"><strong>{data.reply.author}<span aria-hidden="true">↗</span></strong><span className="tr-reply-snippet">{data.reply.text}</span></a><button className="tr-expand" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?'Свернуть':'Весь фрагмент'}</button></div><Body/></>;
}
export function VariantB(){return <><details className="tr-reply-disclosure"><summary>В ответ <strong>{data.reply.author}</strong></summary><div className="tr-disclosure-content"><p>{data.reply.text}</p><a href={data.reply.source_url} {...external}>Открыть цитируемый пост ↗</a></div></details><Body/></>;}
export function VariantC(){return <><Body/><aside className="tr-context"><span className="tr-context-mark" aria-hidden="true">↳</span><div><p className="tr-context-label">В ответ на сообщение</p><a href={data.reply.source_url} {...external}>{data.reply.author} ↗</a><blockquote>{data.reply.text}</blockquote></div></aside></>;}

function PrototypeSwitcher({variant,cycle}:{variant:Variant;cycle:(delta:number)=>void}){return <aside className="tr-switcher" aria-label="Варианты прототипа"><div className="tr-switcher-controls"><button aria-label="Предыдущий вариант" onClick={()=>cycle(-1)}>←</button><strong>{variant} · {names[variant]}</strong><button aria-label="Следующий вариант" onClick={()=>cycle(1)}>→</button></div><small>ПРОТОТИП · пост 75124 · ответ на 75122 · заголовок из собственного текста</small><details className="tr-state"><summary>Данные макета</summary><pre>{JSON.stringify({variant,...data},null,2)}</pre></details></aside>;}
function Preview(){
 const read=():Variant=>{const v=new URLSearchParams(location.search).get('variant');return v==='B'||v==='C'?v:'A';};
 const[variant,setVariant]=useState<Variant>(read);
 function cycle(delta:number){const all:Variant[]=['A','B','C'];const next=all[(all.indexOf(variant)+delta+3)%3]!;const url=new URL(location.href);url.searchParams.set('variant',next);history.replaceState(null,'',url);setVariant(next);}
 useEffect(()=>{const key=(event:KeyboardEvent)=>{if(event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable]')||event.altKey||event.metaKey||event.ctrlKey)return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();cycle(event.key==='ArrowRight'?1:-1);}};const pop=()=>setVariant(read());window.addEventListener('keydown',key);window.addEventListener('popstate',pop);return()=>{window.removeEventListener('keydown',key);window.removeEventListener('popstate',pop);};},[variant]);
 return <><div className="tr-material"><p className="meta">Fully saved · Varlamov News</p><h1><a href={data.source_url} {...external}>{data.title}</a></h1><p className="tr-date"><time dateTime={data.published_at}>{new Date(data.published_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})}</time></p><div key={variant}>{variant==='A'?<VariantA/>:variant==='B'?<VariantB/>:<VariantC/>}</div><footer className="tr-footer"><a href={data.source_url} {...external}>Оригинал публикации ↗</a><button disabled>Просмотрено</button></footer></div><PrototypeSwitcher variant={variant} cycle={cycle}/></>;
}
if(import.meta.env.DEV){
 initializeReaderShell(<ReaderShell/>);
 for(const button of document.querySelectorAll<HTMLButtonElement>('button'))button.disabled=true;
 document.getElementById('progress')!.textContent='Прототип · снимок публичного поста · без подключения к подпискам';
 document.getElementById('reading-nav')!.innerHTML='<h2>Чтение</h2><p class="tr-folder">▾ Новости <span>2</span></p><p>　Varlamov News</p><p>　Другие источники</p>';
 document.getElementById('material-list')!.innerHTML='<h2>Новости</h2><div class="tr-list-card selected"><small>Varlamov News · 21 октября, 15:09</small><h3></h3><span>↳ Ответ на сообщение</span></div><div class="tr-list-card"><small>Синтетический пример</small><h3>Следующая публикация</h3></div>';
 document.querySelector('.tr-list-card h3')!.textContent=data.title;
 mountSurface(document.getElementById('material')!,<Preview/>);
}
