import './post-web-unavailable.css';
export const postWebUnavailableLabel='Пост недоступен для веб-просмотра';
export function TelegramPostIcon(){return <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M21 3 3 10c-1 .4-1 1 .1 1.3l4.5 1.4L18 6.4 9.7 14.2l-.3 4.6 3-3 4.8 3.5c.8.5 1.3.2 1.5-.6L22 4c.2-1-.2-1.3-1-1Z"/></svg>;}
export function PostWebBadge(){return <span className="post-web-badge" role="img" aria-label={postWebUnavailableLabel} title={postWebUnavailableLabel}><TelegramPostIcon/></span>;}
export function PostWebUnavailable({sourceUrl,sourceTitle,retained=false,id}:{sourceUrl:string;sourceTitle?:string;retained?:boolean;id?:string}){
 let url:URL;
 try{url=new URL(sourceUrl);if(url.protocol!=='https:'||url.username||url.password||!['t.me','telegram.me'].includes(url.hostname)||!/^\/[A-Za-z0-9_]+\/[1-9][0-9]*$/.test(url.pathname))return null;}catch{return null;}
 const [username,number]=url.pathname.slice(1).split('/');
 return <section id={id} className={'post-web-card'+(retained?' retained':'')} aria-label={postWebUnavailableLabel}><div className="post-web-banner"><TelegramPostIcon/><span>TELEGRAM · ВЕБ-ПРОСМОТР НЕДОСТУПЕН</span></div><h2>{sourceTitle||username} · пост №{number}</h2><p>Telegram не предоставляет содержимое этого поста для веб-просмотра.{retained?' Ранее сохранённая копия доступна ниже.':''}</p><div className="post-web-footer"><span>{url.host+url.pathname}</span><a href={url.href} target="_blank" rel="noopener noreferrer">Открыть в Telegram ↗</a></div></section>;
}
