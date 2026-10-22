import MarkdownIt,{type Token} from 'markdown-it';
import {createElement,Fragment,useMemo,type ReactNode} from 'react';
import './markdown.css';
import {normalizeMarkdownLinks} from './markdown-links';

const parser=new MarkdownIt({html:false,linkify:true,breaks:true});
// Parse destinations even when unsafe so their readable labels can survive.
// Only safeHref below can grant navigation; no parser HTML is ever inserted.
parser.validateLink=()=>true;
function safeHref(value:string,sourceUrl:string|undefined):string|undefined {
  try {
    const base=sourceUrl&&new URL(sourceUrl);
    const url=new URL(value,base&&['http:','https:'].includes(base.protocol)?base:undefined);
    return ['http:','https:','mailto:'].includes(url.protocol)?url.href:undefined;
  }catch{return undefined;}
}
function renderTokens(tokens:Token[],sourceUrl:string|undefined):ReactNode[] {
  let index=0;
  function children():ReactNode[] {
    const result:ReactNode[]=[];
    while(index<tokens.length){
      const token=tokens[index++]!,key=index;
      const loadError=token.type==='blockquote_open'&&tokens[index]?.type==='paragraph_open'&&tokens[index+1]?.content==='[!load-error]';
      if(loadError)index+=3;
      if(token.nesting===-1)break;
      let content:ReactNode=token.children?renderTokens(token.children,sourceUrl):token.content;
      if(token.nesting===1)content=children();
      if(token.hidden){result.push(<Fragment key={key}>{content}</Fragment>);continue;}
      if(token.type==='inline'){result.push(<Fragment key={key}>{content}</Fragment>);continue;}
      if(token.type==='text'||token.type==='image'){result.push(<Fragment key={key}>{token.content}</Fragment>);continue;}
      if(token.type==='softbreak'||token.type==='hardbreak'){result.push(<Fragment key={key}><br/>{'\n'}</Fragment>);continue;}
      if(token.type==='code_inline'){result.push(<code key={key}>{token.content}</code>);continue;}
      if(token.type==='fence'||token.type==='code_block'){result.push(<pre key={key} data-reading-paragraph><code>{token.content}</code></pre>);continue;}
      if(token.type==='link_open'){
        const href=safeHref(String(token.attrGet('href')||''),sourceUrl);
        result.push(href?<a key={key} href={href} title={token.attrGet('title')?String(token.attrGet('title')):undefined} {...(href.startsWith('mailto:')?{}:{target:'_blank',rel:'noopener noreferrer'})}>{content}</a>:<Fragment key={key}>{content}</Fragment>);continue;
      }
      const tag=/^h[1-6]$/.test(token.tag)?'h'+Math.min(6,Number(token.tag[1])+2):token.tag;
      if(['p','h3','h4','h5','h6','ul','ol','li','blockquote','strong','em','s','hr'].includes(tag)){
        result.push(createElement(tag,{key,...(loadError?{className:'publication-load-error'}:{}),...(['p','li','h3','h4','h5','h6'].includes(tag)?{'data-reading-paragraph':true}:{}),...(tag==='ol'&&token.attrGet('start')?{start:Number(token.attrGet('start'))}:{})},tag==='hr'?undefined:content));
        if(token.block)result.push('\n');
      }else result.push(<Fragment key={key}>{content}</Fragment>);
    }
    return result;
  }
  return children();
}
export function Markdown({text,sourceUrl}:{text:string;sourceUrl?:string}) {
  const nodes=useMemo(()=>renderTokens(parser.parse(normalizeMarkdownLinks(text,parser),{}),sourceUrl),[text,sourceUrl]);
  return <div className="publication-markdown">{nodes}</div>;
}
