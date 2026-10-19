import {useEffect,useLayoutEffect} from 'react';
import type {ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {useStore,host} from './store';
import {createCompletion} from './completion';

export function createFolderActions({readerApi,getFolder,isStopped,onSettled}:{readerApi:ReaderApi;getFolder:()=>string|null;isStopped:()=>boolean;onSettled:(folder:string)=>void}) {
  const completion=createCompletion({readerApi,isStopped,onSettled:target=>onSettled(target.id)});
  function openCompletion() {const folder=getFolder();if(folder)return completion.open({scope:'folder',id:folder});}
  function Completion() {
    const state=useStore(completion),open=!!state.target;
    useLayoutEffect(()=>{const dialog=host('completion-dialog') as HTMLDialogElement;if(open&&!dialog.open)dialog.showModal();else if(!open&&dialog.open)dialog.close();},[open]);
    useEffect(()=>{const dialog=host('completion-dialog');const close=()=>{if(!(dialog as HTMLDialogElement).open&&completion.get().target)completion.close();};dialog.addEventListener('close',close);return()=>dialog.removeEventListener('close',close);},[]);
    return <><h1 id="completion-heading">Mark Folder as Read</h1><p id="completion-count">{state.preview?`Cards: ${state.preview.cards_count}. Original publications: ${state.preview.publications_count}.`:state.loading?'Counting…':'No current confirmation'}</p><p>Text, media and summaries for this entire folder, including hidden items, will be deleted. Other folders will be kept.</p><button id="completion-confirm" disabled={!state.canConfirm} onClick={()=>void completion.confirm()}>Mark as Read</button><button id="completion-close" onClick={()=>completion.close()}>Cancel</button><p id="completion-message" role="status">{state.message}</p></>;
  }
  mountSurface(host('completion-dialog'),<Completion/>);
  return {openCompletion,stop:completion.close};
}
