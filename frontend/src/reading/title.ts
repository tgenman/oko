/** Keep link labels in headings, including destinations cut off by source previews. */
export function newsTitle(value:string):string {
  let result='',cursor=0;
  for(const match of value.matchAll(/\[([^\]\n]+)\]\(/g)){
    const offset=match.index;
    if(offset<cursor)continue;
    result+=value.slice(cursor,offset)+match[1];
    // Consume the destination separately so balanced URL parentheses are retained
    // in the scan and never leak into the heading.
    let depth=1,quote='';
    let end=offset+match[0].length;
    for(;end<value.length;end++){
      const char=value[end];
      if(char==='\\'){end++;continue;}
      if(quote){if(char===quote)quote='';continue;}
      if(char==='"'&&/\s/.test(value[end-1]||'')){quote=char;continue;}
      if(char==='(')depth++;
      if(char===')'&&!--depth){end++;break;}
    }
    cursor=end;
  }
  return result+value.slice(cursor);
}
