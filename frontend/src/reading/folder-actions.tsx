import {useEffect,useLayoutEffect} from 'react';
import {previewCompletion,completeFolder} from '../api/generated/sdk.gen';
import type {CompletionPreview} from '../api/generated/types.gen';
import {ReaderError,type ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import {createStore,useStore,host,errorMessage} from './store';

type CompletionState={open:boolean;folder:string|null;preview:CompletionPreview|null;message:string;busy:boolean;generation:number};
function conflict(error:unknown):{message:string;preview:CompletionPreview}|null {
  if(!(error instanceof ReaderError)||error.code!=='composition_changed'||!error.detail||typeof error.detail!=='object')return null;
  const detail=error.detail;
  if(!('preview'in detail)||!detail.preview||typeof detail.preview!=='object')return null;
  const preview=detail.preview;
  if(!('cards_count'in preview)||typeof preview.cards_count!=='number'||!('publications_count'in preview)||typeof preview.publications_count!=='number'||!('token'in preview)||typeof preview.token!=='string')return null;
  return {message:error.message,preview:{cards_count:preview.cards_count,publications_count:preview.publications_count,token:preview.token}};
}
export function createFolderActions({readerApi,getFolder,isStopped,onRefresh,onCompleted}:{readerApi:ReaderApi;getFolder:()=>string|null;isStopped:()=>boolean;onRefresh:()=>void;onCompleted:(folder:string)=>void}) {
  const completion=createStore<CompletionState>({open:false,folder:null,preview:null,message:'',busy:false,generation:0});
  const completionActive=(state:CompletionState)=>!isStopped()&&completion.get().open&&completion.get().generation===state.generation&&getFolder()===state.folder;
  function closeCompletion() {const state=completion.get();completion.set({...state,open:false,busy:false,preview:null,generation:state.generation+1});}
  async function openCompletion() {
    const folder=getFolder();if(!folder||isStopped())return;
    const state:CompletionState={open:true,folder,preview:null,message:'',busy:true,generation:completion.get().generation+1};completion.set(state);
    try {const preview=(await previewCompletion({client:readerApi.client,path:{folder}})).data;if(completionActive(state))completion.set({...completion.get(),preview,busy:false});}
    catch(error){reportError(error,'Folder completion');if(completionActive(state))completion.set({...completion.get(),message:errorMessage(error),busy:false});}
  }
  async function confirmCompletion() {
    const state=completion.get();if(!state.folder||!state.preview||state.busy||!completionActive(state))return;
    completion.set({...state,busy:true,message:''});
    try {await readerApi.command({operation:'complete_folder',folder_id:state.folder},headers=>completeFolder({client:readerApi.client,headers,path:{folder:state.folder!},body:{token:state.preview!.token}}));onCompleted(state.folder);if(completionActive(state))closeCompletion();onRefresh();}
    catch(error){reportError(error,'Folder completion');const changed=conflict(error);if(!changed)onCompleted(state.folder);onRefresh();if(completionActive(state))completion.set({...completion.get(),message:changed?.message||errorMessage(error),preview:changed?.preview||state.preview,busy:false});}
  }
  function Completion() {
    const state=useStore(completion);
    useLayoutEffect(()=>{const dialog=host('completion-dialog') as HTMLDialogElement;if(state.open&&!dialog.open)dialog.showModal();else if(!state.open&&dialog.open)dialog.close();},[state.open]);
    useEffect(()=>{const dialog=host('completion-dialog');const close=()=>{if(!(dialog as HTMLDialogElement).open&&completion.get().open)closeCompletion();};dialog.addEventListener('close',close);return()=>dialog.removeEventListener('close',close);},[]);
    return <><h1 id="completion-heading">Mark Folder as Read</h1><p id="completion-count">{state.preview?`Cards: ${state.preview.cards_count}. Original publications: ${state.preview.publications_count}.`:'Counting…'}</p><p>Text, media and summaries for this entire folder, including hidden items, will be deleted. Other folders will be kept.</p><button id="completion-confirm" disabled={isStopped()||state.busy||!state.preview?.publications_count} onClick={()=>void confirmCompletion()}>Mark as Read</button><button id="completion-close" onClick={closeCompletion}>Cancel</button><p id="completion-message" role="status">{state.message}</p></>;
  }
  mountSurface(host('completion-dialog'),<Completion/>);
  return {
    openCompletion,
    stop(){closeCompletion();},
  };
}
