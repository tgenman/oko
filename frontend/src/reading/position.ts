// Keep the visible paragraph at its screen position, then fall back to a
// neighbouring paragraph or relative progress when the text was rewritten.
export function readingPosition() {
  const article=document.querySelector('article');
  if(!article)return ()=>{};
  const paragraphs=()=>Array.from(article.querySelectorAll<HTMLElement>('[data-reading-paragraph]')).filter(node=>node.getClientRects().length);
  const top=article.getBoundingClientRect().top,nodes=paragraphs();
  const index=nodes.findIndex(node=>node.getBoundingClientRect().bottom>top);
  const section=nodes[index]?.parentElement?.id;
  const candidates=nodes.map((node,index)=>({text:node.textContent,section:node.parentElement?.id,offset:node.getBoundingClientRect().top-top,index})).filter(node=>node.section===section).sort((a,b)=>Math.abs(a.index-index)-Math.abs(b.index-index));
  const progress=article.scrollTop/Math.max(1,article.scrollHeight-article.clientHeight),scroll=article.scrollTop;
  return ()=>{
    const next=new Map<string|null,{node:HTMLElement;index:number}[]>();
    paragraphs().forEach((node,index)=>{if(node.parentElement?.id!==section)return;const matches=next.get(node.textContent)||[];matches.push({node,index});next.set(node.textContent,matches);});
    for(const candidate of candidates){
      const matches=next.get(candidate.text);
      if(!matches?.length)continue;
      const closest=matches.reduce((a,b)=>Math.abs(a.index-candidate.index)<=Math.abs(b.index-candidate.index)?a:b).node;
      article.scrollTop+=closest.getBoundingClientRect().top-article.getBoundingClientRect().top-candidate.offset;return;
    }
    article.scrollTop=nodes.length?progress*Math.max(0,article.scrollHeight-article.clientHeight):scroll;
  };
}
