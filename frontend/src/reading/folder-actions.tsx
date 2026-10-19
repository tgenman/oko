import {useEffect,useLayoutEffect} from 'react';
import type {ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {useStore,host} from './store';
import {createCompletion} from './completion';

export function createFolderActions({readerApi,getFolder,isStopped,onSettled}:{readerApi:ReaderApi;getFolder:()=>string|null;isStopped:()=>boolean;onSettled:(folder:string)=>void}) {
  let presentation:{title:string;trigger:HTMLElement|null}={title:'',trigger:null};
  const restoreFocus=()=>{if(presentation.trigger?.isConnected)presentation.trigger.focus({preventScroll:true});};
  const completion=createCompletion({readerApi,isStopped,onSettled:target=>onSettled(target.id),onConfirmed:restoreFocus});
  function closeCompletion(){completion.close();restoreFocus();}
  function openCompletion(folder=getFolder(),title='',trigger:HTMLElement|null=document.activeElement as HTMLElement) {
    if(!folder||isStopped())return;
    presentation={title,trigger};return completion.open({scope:'folder',id:folder});
  }
  function Completion() {
    const state=useStore(completion),open=!!state.target;
    useLayoutEffect(()=>{const dialog=host('completion-dialog') as HTMLDialogElement;if(open&&!dialog.open)dialog.showModal();else if(!open&&dialog.open)dialog.close();},[open]);
    useEffect(()=>{const dialog=host('completion-dialog');const close=()=>{if(!(dialog as HTMLDialogElement).open&&completion.get().target)closeCompletion();};dialog.addEventListener('close',close);return()=>dialog.removeEventListener('close',close);},[]);
    return <><h1 id="completion-heading">Mark All as Read{presentation.title?' · '+presentation.title:''}</h1><p id="completion-count">{state.preview?`Cards: ${state.preview.cards_count}. Original publications: ${state.preview.publications_count}.`:state.loading?'Counting…':'No current confirmation'}</p><p>Collected text, versions, media and processing results for this folder, including hidden and unloaded items, will be deleted. Feeds stay connected. Telegram read status will sync according to your current settings.</p><button id="completion-confirm" disabled={!state.canConfirm} onClick={()=>void completion.confirm()}>Mark All as Read</button><button id="completion-close" onClick={closeCompletion}>Cancel</button><p id="completion-message" role="status">{state.message}</p></>;
  }
  mountSurface(host('completion-dialog'),<Completion/>);
  return {openCompletion,update(folders:{id:string}[]){const target=completion.get().target;if(target&&!folders.some(folder=>folder.id===target.id))closeCompletion();},stop:closeCompletion};
}
