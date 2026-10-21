import type {ReactNode} from 'react';
import type {ExternalAttachment} from '../api/generated/types.gen';
import './external-attachments.css';
type Kind=ExternalAttachment['kind'];
const labels:Record<Kind,string>={audio:'Аудио доступно в Telegram',video:'Видео доступно в Telegram',image:'Фото доступно в Telegram',document:'Файл доступен в Telegram',unsupported:'Вложение доступно в Telegram'};
export const externalMediaLabel=(kind:Kind)=>labels[kind];
export function ExternalMediaIcon({kind}:{kind:Kind}){return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{kind==='audio'?<><path d="M9 17V6l11-3v11M9 9l11-3"/><ellipse cx="6" cy="18" rx="3" ry="2.5"/><ellipse cx="17" cy="15" rx="3" ry="2.5"/></>:kind==='video'?<><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></>:kind==='image'?<><rect x="3" y="3" width="18" height="18" rx="2"/><path d="m3 17 6-6 4 4 3-3 5 5"/></>:<><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8zM14 3v5h5M8 12h8M8 16h6"/></>}</svg>;}
export function ExternalBadges({kinds=[]}:{kinds?:Kind[]}){return <span className="external-media-badges" data-tooltip={[...new Set(kinds)].map(externalMediaLabel).join(' · ')}>{[...new Set(kinds)].map(kind=><span key={kind} role="img" aria-label={labels[kind]} title={labels[kind]}><ExternalMediaIcon kind={kind}/></span>)}</span>;}
export function ExternalAttachments({attachments=[],poster}:{attachments?:ExternalAttachment[];poster(url:string):ReactNode}){return <>{attachments.map((media,index)=>{
 const sized=media.kind==='video'&&media.restriction==='size_limit';
 const title=sized?'Видео слишком большое для веб-просмотра':media.title||labels[media.kind];
 let href:string|undefined;
 try{const url=new URL(media.source_url);if(url.protocol==='https:'&&!url.username&&!url.password)href=url.href;}catch{/* An invalid saved link does not hide the notice. */}
 return <div className="external-attachment" key={media.source_url+'/'+media.kind+'/'+index}>
 <div className="external-attachment-art">{media.poster_url?poster(media.poster_url):<ExternalMediaIcon kind={media.kind}/>}</div>
 <div className="external-attachment-copy"><small>{media.kind==='audio'?'Аудио':media.kind==='video'?'Видео':media.kind==='image'?'Фото':'Вложение'}{media.duration?' · '+media.duration:''} · в Telegram</small><h3>{title}</h3>{media.author&&<p>{media.author}</p>}{media.kind==='audio'&&<p>Аудиофайл недоступен через публичный просмотр Telegram. Текст поста сохранён ниже.</p>}{href&&<a href={href} target="_blank" rel="noopener noreferrer">{media.kind==='audio'?'Слушать в Telegram':media.kind==='video'?'Смотреть в Telegram':'Открыть в Telegram'} ↗</a>}</div>
 </div>;
 })}</>;}
