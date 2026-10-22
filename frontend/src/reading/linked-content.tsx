import type {ReactNode} from 'react';
import type {LinkedContent} from '../api/generated/types.gen';
import {Markdown} from './markdown';
import './linked-content.css';

export function LinkedPublication({content,renderMedia}:{content:LinkedContent;renderMedia:(urls:string[])=>ReactNode}) {
 return <><div className="telegram-post"><p className="linked-content-label">Пост в Telegram</p><Markdown text={content.post_text}/></div>{content.articles.map(article=><section key={article.source_url} className="telegraph-article" aria-label="Статья Telegraph">
  <header><span className="linked-content-label">Статья по ссылке · Telegraph</span><a href={article.source_url} target="_blank" rel="noopener noreferrer">Оригинал ↗</a></header>
  <div className="telegraph-body"><h2>{article.title}</h2>{article.error&&<p className="telegraph-error">{article.error}</p>}{article.text?<Markdown text={article.text} sourceUrl={article.source_url}/>:<p>Содержимое статьи недоступно. Ссылка и причина ошибки сохранены в посте.</p>}{renderMedia(article.media_urls)}</div>
  <footer>Конец статьи Telegraph</footer>
 </section>)}</>;
}
