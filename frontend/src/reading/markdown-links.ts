import type {MarkdownIt,Token} from 'markdown-it';

// Repair only explicitly delimited Markdown links. Code blocks are identified
// by the parser; balanced inline code, escapes and nested brackets are skipped.
function codeEnd(source:string,start:number):number {
  const run=/^`+/.exec(source.slice(start))![0];
  let cursor=start+run.length;
  while((cursor=source.indexOf(run,cursor))!==-1){
    if(source[cursor-1]!=='`'&&source[cursor+run.length]!=='`')return cursor+run.length;
    cursor+=run.length;
  }
  return start;
}
function closing(source:string,start:number,open:string,close:string):number {
  let depth=1,quote='';
  for(let i=start+1;i<source.length;i++){
    const char=source[i];
    if(char==='\\'){i++;continue;}
    if(open==='('&&quote){if(char===quote)quote='';continue;}
    if(open==='('&&(char==='"'||char==="'")&&/\s/.test(source[i-1]||'')){quote=char;continue;}
    if(char==='`'){const end=codeEnd(source,i);if(end>i){i=end-1;continue;}}
    if(char===open)depth++;
    if(char===close&&!--depth)return i;
  }
  return -1;
}
function label(value:string):string {return value.replace(/[ \t]*\n[\s]*/g,' ').trim();}
function destination(value:string):string {
  const title=value.search(/[ \t\n]+(?=["'])/);
  return title<0?value.replace(/[ \t]*\n[ \t]*/g,''):value.slice(0,title).replace(/[ \t]*\n[ \t]*/g,'')+value.slice(title).replace(/[ \t]*\n[ \t]*/g,' ');
}
export function normalizeMarkdownLinks(text:string,parser:MarkdownIt):string {
  const source=text.replace(/\r\n?/g,'\n'),blocks:Token[]=[];
  parser.block.parse(source,parser,{},blocks);
  const offsets=[0];for(let i=0;i<source.length;i++)if(source[i]==='\n')offsets.push(i+1);
  const protectedRanges=blocks.filter(t=>(t.type==='fence'||t.type==='code_block')&&t.map).map(t=>[offsets[t.map![0]]!,offsets[t.map![1]]??source.length]);
  let result='',cursor=0,rangeIndex=0;
  for(let i=0;i<source.length;){
    while(rangeIndex<protectedRanges.length&&protectedRanges[rangeIndex]![1]!<=i)rangeIndex++;
    const range=protectedRanges[rangeIndex];
    if(range&&i>=range[0]!&&i<range[1]!){i=range[1]!;continue;}
    if(source[i]==='\\'){i+=2;continue;}
    if(source[i]==='`'){const end=codeEnd(source,i);if(end>i){i=end;continue;}}
    if(source[i]!=='['){i++;continue;}
    const end=closing(source,i,'[',']');
    if(end<0){i++;continue;}
    let next=end+1;while(/[ \t\n]/.test(source[next]||'')&&next<source.length)next++;
    let replacement:string|undefined,stop=end+1;
    if(source[next]==='('){
      const close=closing(source,next,'(',')');
      if(close>=0&&!protectedRanges.some(([start,finish])=>start!<close&&finish!>i)){
        replacement='['+label(source.slice(i+1,end))+']('+destination(source.slice(next+1,close))+')';stop=close+1;
      }
    }else if(source[end+1]===':'&&/(?:^|\n) {0,3}$/.test(source.slice(Math.max(0,i-4),i))){
      // A bare reference destination ends at its line break. An indented next
      // line may be prose or code, so only explicit <...> can span lines.
      const gap=/^[ \t]*(?:\n[ \t]*)?/.exec(source.slice(end+2))![0];
      const start=end+2+gap.length,close=source[start]==='<'?source.indexOf('>',start+1):-1;
      if(close>=0&&!protectedRanges.some(([first,last])=>first!<close&&last!>i)){
        replacement='['+label(source.slice(i+1,end))+']:'+gap+'<'+destination(source.slice(start+1,close))+'>';stop=close+1;
      }else{
        replacement='['+label(source.slice(i+1,end))+']:';stop=end+2;
      }
    }else if(source[next]==='['){
      const close=closing(source,next,'[',']');
      if(close>=0){replacement='['+label(source.slice(i+1,end))+']['+label(source.slice(next+1,close))+']';stop=close+1;}
    }
    if(replacement!==undefined){result+=source.slice(cursor,i)+replacement;cursor=stop;i=stop;}else i=end+1;
  }
  return result+source.slice(cursor);
}
