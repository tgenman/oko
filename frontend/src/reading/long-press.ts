import {useEffect,useRef} from 'react';
import type {MouseEvent,PointerEvent} from 'react';

type Open=(x:number,y:number,trigger:HTMLElement)=>void;
type Press={id:number;x:number;y:number;target:HTMLElement;timer:number;opened:boolean;finish:(event:globalThis.PointerEvent)=>void;other:(event:globalThis.PointerEvent)=>void};

// A completed touch hold owns its subsequent click and native contextmenu.
export function useLongPress(){
 const press=useRef<Press|null>(null);
 const clear=()=>{const p=press.current;if(!p)return;window.clearTimeout(p.timer);window.removeEventListener('pointerdown',p.other);window.removeEventListener('pointerup',p.finish);window.removeEventListener('pointercancel',p.finish);press.current=null;};
 useEffect(()=>()=>clear(),[]);
 return (open:Open)=>({
  onPointerDown:(event:PointerEvent<HTMLElement>)=>{
   if(event.pointerType!=='touch')return;
   if(!event.isPrimary){if(!press.current?.opened)clear();return;}
   clear();const target=event.currentTarget,x=event.clientX,y=event.clientY,id=event.pointerId;
   const current:Press={id,x,y,target,opened:false,timer:0,finish:()=>{},other:()=>{}};
   current.other=(next)=>{if(next.pointerType==='touch'&&next.pointerId!==id&&!current.opened)clear();};
   current.finish=(end)=>{
    if(press.current!==current||end.pointerId!==id)return;
    window.removeEventListener('pointerdown',current.other);window.removeEventListener('pointerup',current.finish);window.removeEventListener('pointercancel',current.finish);window.clearTimeout(current.timer);
    if(!current.opened){clear();return;}
    window.requestAnimationFrame(()=>{if(document.activeElement===target||document.activeElement===document.body)document.querySelector<HTMLElement>('#news-context-menu [role=menuitem]:not(:disabled),#feed-context-menu [role=menuitem]:not(:disabled),#folder-context-menu [role=menuitem]:not(:disabled)')?.focus({preventScroll:true});});
    current.timer=window.setTimeout(()=>{if(press.current===current)clear();},1000);
   };
   press.current=current;window.addEventListener('pointerdown',current.other);window.addEventListener('pointerup',current.finish);window.addEventListener('pointercancel',current.finish);
   current.timer=window.setTimeout(()=>{if(press.current!==current||!target.isConnected)return;current.opened=true;open(x,y,target);},500);
  },
  onPointerMove:(event:PointerEvent<HTMLElement>)=>{const p=press.current;if(p&&p.id===event.pointerId&&!p.opened&&Math.hypot(event.clientX-p.x,event.clientY-p.y)>10)clear();},
  onClickCapture:(event:MouseEvent<HTMLElement>)=>{const p=press.current;if(p?.opened&&p.target===event.currentTarget&&event.detail!==0){event.preventDefault();event.stopPropagation();clear();}},
  onContextMenu:(event:MouseEvent<HTMLElement>)=>{event.preventDefault();const p=press.current;if(p&&p.target===event.currentTarget){if(p.opened)return;window.clearTimeout(p.timer);p.opened=true;open(p.x,p.y,p.target);return;}clear();open(event.clientX,event.clientY,event.currentTarget);},
 });
}
