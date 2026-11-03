import type {StatusSummary} from '../api/generated/types.gen';
import {mountSurface} from '../renderer';
import './page.css';

// Navigation owns only the visible page; existing reader/settings owners keep
// their drafts and reading selection. Direct URLs are served by the same origin.
export function createAdminPage(){
 const node=(id:string)=>document.getElementById(id)!;
 let onProblems:()=>void=()=>{},onReading:()=>void=()=>{};
 function apply(focus=false){
  const admin=location.pathname==='/admin';
  node('admin-page').hidden=!admin;node('reading-page').hidden=admin;
  node('reader-shell').classList.toggle('show-admin',admin);
  if(!admin)onReading();
  node('reader-link').hidden=!admin;node('app-menu').hidden=admin;
  if(focus)(admin?node('admin-title'):node('admin-link')).focus();
 }
 function navigate(admin:boolean,problems=false){
  const url=new URL(location.href);url.pathname=admin?'/admin':'/';url.search='';
  if(problems)url.searchParams.set('problems','1');
  history.pushState(null,'',url);(node('app-menu') as HTMLDetailsElement).open=false;apply(true);
  if(problems)onProblems();
 }
 for(const [id,admin,problems]of [['admin-link',true,false],['reader-link',false,false],['reader-problems',true,true]]as const){
  (node(id) as HTMLAnchorElement).hash=location.hash;
  node(id).onclick=event=>{if(event instanceof MouseEvent&&(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey))return;event.preventDefault();navigate(admin,problems);};
 }
 const menu=node('app-menu') as HTMLDetailsElement;
 function positionMenu(){
  if(!menu.open)return;
  const trigger=menu.querySelector('summary')!.getBoundingClientRect();
  const scrollport=document.querySelector('.reader-toolbar-scroll')!.getBoundingClientRect();
  if(trigger.left<scrollport.left||trigger.right>scrollport.right){menu.open=false;return;}
  menu.style.setProperty('--menu-top',`${trigger.bottom+6}px`);
  menu.style.setProperty('--menu-right',`${Math.max(8,window.innerWidth-trigger.right)}px`);
 }
 menu.addEventListener('toggle',positionMenu);
 document.querySelector('.reader-toolbar-scroll')!.addEventListener('scroll',positionMenu);
 window.addEventListener('resize',positionMenu);
 node('admin-problems').onclick=()=>onProblems();
 window.addEventListener('popstate',()=>{apply();if(location.pathname==='/admin'&&new URLSearchParams(location.search).has('problems'))onProblems();});
 apply();
 function update(value:StatusSummary|undefined,count:number,online:boolean|null,stopped:boolean){
  const unavailable=!online;
  const power=stopped?'Stopped':unavailable?'No connection':value?.runtime_state==='running'?'Running':value?.runtime_state==='unavailable'?'Unavailable':value?.runtime_state==='stopping'?'Stopping…':'Stopped';
  node('admin-power').textContent=power;node('admin-power').classList.toggle('running',Boolean(online&&value?.runtime_state==='running'&&value.work_allowed&&!stopped));
  const indicator=node('reader-problems');indicator.hidden=count===0&&Boolean(online)&&!stopped;
  indicator.textContent=count?`● ${count}`:'!';indicator.setAttribute('aria-label',count?`Oko problems: ${count}`:power);
  const current=value?.problems||[];
  mountSurface(node('admin-attention'),<><h3>{unavailable?'No connection to Oko':count?`${count} ${count===1?'problem needs':'problems need'} attention`:'No active problems'}</h3><p>{unavailable?'Status may be outdated. Open Problems to check the connection.':current.length?'Open Problems to review affected sources and recovery actions.':count?'Review the latest action in Problems.':'Nothing needs your attention right now.'}</p></>);
  const progress=(part:StatusSummary['collection']|undefined)=>!online||value?.runtime_state==='unavailable'?'Unavailable':value?.runtime_state==='stopping'?'Stopping…':!value?.work_allowed?'Stopped':part?.state==='running'?'Running':'Idle';
  mountSurface(node('admin-work'),<><div><span>Source collection</span><strong>{progress(value?.collection)}</strong></div><div><span>Processing</span><strong>{progress(value?.processing)}</strong></div><div><span>Sources</span><strong>{online?value?.sources.length??'—':'—'}</strong></div></>);
 }
 return {update,isAdmin:()=>location.pathname==='/admin',showReader:()=>navigate(false),showAdmin(){if(location.pathname!=='/admin')navigate(true);},bindProblems(open:()=>void,close:()=>void){onProblems=open;onReading=close;if(location.pathname==='/admin'&&new URLSearchParams(location.search).has('problems'))queueMicrotask(open);}};
}
