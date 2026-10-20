import './folder-menu.css';
import {useEffect,useLayoutEffect,useRef} from 'react';
import {createPortal} from 'react-dom';
import {renameFolder,removeFolder} from '../api/generated/sdk.gen';
import {ReaderError,type ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import {createStore,useStore,errorMessage} from './store';
import type {Navigation} from './tree';

type Folder=Navigation['folders'][number];
type Target={folder:Folder;x:number;y:number;trigger:HTMLElement};
type State={target:Target|null;dialog:'rename'|'delete'|'blocked'|null;name:string;message:string;busy:boolean;generation:number};
export function createFolderMenu({readerApi,isStopped,canRefresh,onRefresh,onSettings,onRead,onChanged}:{
  readerApi:ReaderApi;isStopped:()=>boolean;canRefresh:()=>boolean;onRefresh:(id:string)=>Promise<void>;
  onSettings:(id:string,trigger:HTMLElement)=>void;
  onRead:(id:string,title:string,trigger:HTMLElement)=>void;
  onChanged:(id:string,removed:boolean)=>Promise<unknown>;
}){
  const store=createStore<State>({target:null,dialog:null,name:'',message:'',busy:false,generation:0});
  const pending=new Set<string>();let catalog:Navigation={folders:[],feeds:[],count:0};
  const current=(state:State)=>!isStopped()&&store.get().generation===state.generation&&store.get().target?.folder.id===state.target?.folder.id;
  function close(focus=true){const state=store.get();store.set({...state,target:null,dialog:null,busy:false,message:'',generation:state.generation+1});if(focus&&state.target?.trigger.isConnected)state.target.trigger.focus({preventScroll:true});}
  const nonempty=(id:string)=>catalog.feeds.some(feed=>feed.folder_id===id);
  function prepare(action:'rename'|'delete'){
    const state=store.get();if(!state.target)return;
    store.set({...state,dialog:action==='delete'&&nonempty(state.target.folder.id)?'blocked':action,name:state.target.folder.title,message:''});
  }
  async function mutate(){
    const state=store.get(),target=state.target;if(!target||state.busy||isStopped()||!['rename','delete'].includes(state.dialog||''))return;
    const id=target.folder.id,title=state.name.trim(),removed=state.dialog==='delete';
    if(pending.has(id))return;
    if(!removed){
      if(!title){store.set({...state,message:'Enter a folder name.'});return;}
      if(catalog.folders.some(folder=>folder.id!==id&&folder.title===title)){store.set({...state,message:'A folder with this name already exists.'});return;}
      if(title===target.folder.title){close();return;}
    }
    pending.add(id);store.set({...state,busy:true,message:''});
    try{
      if(removed)await readerApi.command({operation:'remove_folder',folder_id:id},headers=>removeFolder({client:readerApi.client,path:{folder:id},headers}));
      else await readerApi.command({operation:'rename_folder',folder_id:id},headers=>renameFolder({client:readerApi.client,path:{folder:id},body:{title},headers}));
      // Reconcile the accepted write even if its dialog has since been closed.
      await onChanged(id,removed);
      if(current(state))close();
    }catch(error){
      reportError(error,'Folder action');
      if(current(state))store.set({...store.get(),busy:false,message:errorMessage(error),dialog:error instanceof ReaderError&&error.code==='folder_not_empty'?'blocked':store.get().dialog});
    }finally{pending.delete(id);if(store.get().target?.folder.id===id)store.set({...store.get(),busy:false});}
  }
  function Surface(){
    const state=useStore(store),{target,dialog}=state;const ref=useRef<HTMLDivElement>(null),modal=useRef<HTMLDialogElement>(null),name=useRef<HTMLInputElement>(null);
    useLayoutEffect(()=>{if(target&&!dialog)ref.current?.querySelector<HTMLButtonElement>('button')?.focus();},[target?.folder.id,state.generation,dialog]);
    useLayoutEffect(()=>{if(dialog&&modal.current&&!modal.current.open){modal.current.showModal();if(dialog==='rename')name.current?.select();}},[dialog,state.generation]);
    useEffect(()=>{
      if(!target||dialog)return;
      const pointer=(event:PointerEvent)=>{if(!ref.current?.contains(event.target as Node))close(false);};
      const hide=()=>close(false);const scroll=(event:Event)=>{if(!ref.current?.contains(event.target as Node))hide();};
      window.addEventListener('pointerdown',pointer);window.addEventListener('resize',hide);window.addEventListener('scroll',scroll,true);
      return()=>{window.removeEventListener('pointerdown',pointer);window.removeEventListener('resize',hide);window.removeEventListener('scroll',scroll,true);};
    },[target?.folder.id,dialog]);
    if(!target)return null;
    const disabled=state.busy||isStopped()||pending.has(target.folder.id);
    function keys(event:React.KeyboardEvent){
      if(event.key==='Escape'){event.preventDefault();close();return;}
      if(event.key==='Tab'){close(false);return;}
      if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
        event.preventDefault();const options=[...ref.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')],index=options.indexOf(event.target as HTMLButtonElement);
        options[event.key==='Home'?0:event.key==='End'?options.length-1:(index+(event.key==='ArrowDown'?1:options.length-1))%options.length]?.focus();
      }
    }
    return createPortal(<>{!dialog?<div ref={ref} id="folder-context-menu" role="menu" aria-label={target.folder.title} className="feed-context-menu" style={{left:Math.max(8,Math.min(target.x,innerWidth-260)),top:Math.max(8,Math.min(target.y,innerHeight-235))}} onKeyDown={keys}>
      <button role="menuitem" disabled={disabled||!canRefresh()} onClick={()=>{const id=target.folder.id;close();void onRefresh(id);}}><span aria-hidden="true">↻</span>Refresh</button>
      <button role="menuitem" disabled={disabled} onClick={()=>{close(false);onRead(target.folder.id,target.folder.title,target.trigger);}}><span aria-hidden="true">✓</span>Mark All as Read</button>
      <button role="menuitem" disabled={disabled} onClick={()=>{close(false);onSettings(target.folder.id,target.trigger);}}><span aria-hidden="true">⚙</span>Settings…</button>
      <button role="menuitem" disabled={disabled} onClick={()=>prepare('rename')}><span aria-hidden="true">✎</span>Rename…</button><hr/>
      <button role="menuitem" disabled={disabled} className="danger" onClick={()=>prepare('delete')}><span aria-hidden="true">⌫</span>Delete…</button>
    </div>:<dialog id="folder-action-dialog" className="folder-action-dialog" ref={modal} aria-labelledby="folder-action-heading" onCancel={event=>{event.preventDefault();close();}} onClose={()=>{if(store.get().dialog)close();}}>
      <form onSubmit={event=>{event.preventDefault();void mutate();}}>
        <h2 id="folder-action-heading">{dialog==='rename'?'Rename Folder':dialog==='blocked'?'Folder Is Not Empty':'Delete Folder'}</h2>
        {dialog==='rename'?<label>Name<input ref={name} id="folder-rename-name" value={state.name} maxLength={200} disabled={disabled} aria-describedby="folder-action-message" aria-invalid={!!state.message} onChange={event=>store.set({...store.get(),name:event.target.value,message:''})}/></label>:dialog==='blocked'?<p>Move or delete all feeds in “{target.folder.title}” before deleting this folder.</p>:<p>Delete “{target.folder.title}” and its folder settings?</p>}
        <p id="folder-action-message" role="status">{state.message}</p>
        <div className="feed-dialog-actions">{dialog==='blocked'?<button type="button" id="folder-action-close" onClick={()=>close()}>OK</button>:<><button type="button" id="folder-action-cancel" onClick={()=>close()}>Cancel</button><button type="submit" id="folder-action-confirm" className={dialog==='delete'?'danger':''} disabled={disabled}>{dialog==='rename'?'Save':'Delete Folder'}</button></>}</div>
      </form>
    </dialog>}</>,document.body);
  }
  const host=document.createElement('div');document.body.append(host);mountSurface(host,<Surface/>);
  return {close,open(folder:Folder,next:Navigation,x:number,y:number,trigger:HTMLElement){if(isStopped())return;catalog=next;store.set({target:{folder,x,y,trigger},dialog:null,name:'',message:'',busy:pending.has(folder.id),generation:store.get().generation+1});},
    update(next:Navigation){catalog=next;const state=store.get();if(!state.target)return;const folder=next.folders.find(folder=>folder.id===state.target!.folder.id);if(!folder){close(false);return;}store.set({...state,target:{...state.target,folder},dialog:state.dialog==='blocked'&&!nonempty(folder.id)?'delete':state.dialog==='delete'&&nonempty(folder.id)?'blocked':state.dialog});},stop(){close(false);}};
}
