import {useEffect,useLayoutEffect} from 'react';
import {createDigest,previewCompletion,completeFolder} from '../api/generated/sdk.gen';
import type {CompletionPreview,DigestRequest,CompletionConflict} from '../api/generated/types.gen';
import {ReaderError,type ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import {createStore,useStore,host,errorMessage} from './store';

type DigestState={open:boolean;folder:string|null;period:boolean;start:string;end:string;message:string;busy:boolean;generation:number};
type CompletionState={open:boolean;folder:string|null;preview:CompletionPreview|null;message:string;busy:boolean;generation:number};
function conflict(error:unknown):CompletionConflict|null {
  if(!(error instanceof ReaderError)||!error.detail||typeof error.detail!=='object')return null;
  const detail=error.detail;
  if(!('message'in detail)||typeof detail.message!=='string'||!('preview'in detail)||!detail.preview||typeof detail.preview!=='object')return null;
  const preview=detail.preview;
  if(!('cards'in preview)||typeof preview.cards!=='number'||!('originals'in preview)||typeof preview.originals!=='number'||!('token'in preview)||typeof preview.token!=='string')return null;
  return {message:detail.message,preview:{cards:preview.cards,originals:preview.originals,token:preview.token}};
}
export function createFolderActions({readerApi,getFolder,isStopped,onRefresh,onCompleted}:{readerApi:ReaderApi;getFolder:()=>string|null;isStopped:()=>boolean;onRefresh:()=>void;onCompleted:(folder:string)=>void}) {
  const digest=createStore<DigestState>({open:false,folder:null,period:false,start:'',end:'',message:'',busy:false,generation:0});
  const completion=createStore<CompletionState>({open:false,folder:null,preview:null,message:'',busy:false,generation:0});
  const digestActive=(state:DigestState)=>!isStopped()&&digest.get().open&&digest.get().generation===state.generation&&getFolder()===state.folder;
  const completionActive=(state:CompletionState)=>!isStopped()&&completion.get().open&&completion.get().generation===state.generation&&getFolder()===state.folder;
  function closeDigest() {const state=digest.get();digest.set({...state,open:false,busy:false,generation:state.generation+1});}
  function closeCompletion() {const state=completion.get();completion.set({...state,open:false,busy:false,preview:null,generation:state.generation+1});}
  async function runDigest() {
    const state=digest.get();if(!state.folder||state.busy||!digestActive(state))return;
    digest.set({...state,busy:true,message:''});const body:DigestRequest={period:state.period};
    if(state.start)body.start=new Date(state.start).getTime()/1000;if(state.end)body.end=new Date(state.end).getTime()/1000;
    try {await createDigest({client:readerApi.client,path:{scope:'folder',owner:state.folder},body});if(digestActive(state))closeDigest();onRefresh();}
    catch(error){reportError(error,'Обзор папки');if(digestActive(state))digest.set({...digest.get(),message:errorMessage(error)});}
    finally {if(digestActive(state))digest.set({...digest.get(),busy:false});}
  }
  async function openCompletion() {
    const folder=getFolder();if(!folder||isStopped())return;
    const state:CompletionState={open:true,folder,preview:null,message:'',busy:true,generation:completion.get().generation+1};completion.set(state);
    try {const preview=(await previewCompletion({client:readerApi.client,path:{folder}})).data;if(completionActive(state))completion.set({...completion.get(),preview,busy:false});}
    catch(error){reportError(error,'Завершение папки');if(completionActive(state))completion.set({...completion.get(),message:errorMessage(error),busy:false});}
  }
  async function confirmCompletion() {
    const state=completion.get();if(!state.folder||!state.preview||state.busy||!completionActive(state))return;
    completion.set({...state,busy:true,message:''});
    try {await completeFolder({client:readerApi.client,path:{folder:state.folder},body:{token:state.preview.token}});onCompleted(state.folder);if(completionActive(state))closeCompletion();onRefresh();}
    catch(error){reportError(error,'Завершение папки');const changed=conflict(error);if(!changed)onCompleted(state.folder);onRefresh();if(completionActive(state))completion.set({...completion.get(),message:changed?.message||errorMessage(error),preview:changed?.preview||state.preview,busy:false});}
  }
  function Digest() {
    const state=useStore(digest);
    useLayoutEffect(()=>{const dialog=host('digest-dialog') as HTMLDialogElement;if(state.open&&!dialog.open)dialog.showModal();else if(!state.open&&dialog.open)dialog.close();},[state.open]);
    useEffect(()=>{const dialog=host('digest-dialog');const close=()=>{if(!(dialog as HTMLDialogElement).open&&digest.get().open)closeDigest();};dialog.addEventListener('close',close);return()=>dialog.removeEventListener('close',close);},[]);
    return <><h1 id="digest-heading">{state.period?'Обновить обзор за период':'Сжать историю папки'}</h1><p>По умолчанию — всё непросмотренное. Оригиналы останутся доступны до отметки «Просмотрено».</p><label>С даты (необязательно) <input id="digest-start" type="datetime-local" value={state.start} onChange={event=>digest.set({...digest.get(),start:event.currentTarget.value})}/></label><label>По дату (необязательно) <input id="digest-end" type="datetime-local" value={state.end} onChange={event=>digest.set({...digest.get(),end:event.currentTarget.value})}/></label><button id="digest-run" disabled={state.busy||isStopped()} onClick={()=>void runDigest()}>Создать сводку</button><button id="digest-close" onClick={closeDigest}>Отмена</button><p id="digest-message" role="status">{state.message}</p></>;
  }
  function Completion() {
    const state=useStore(completion);
    useLayoutEffect(()=>{const dialog=host('completion-dialog') as HTMLDialogElement;if(state.open&&!dialog.open)dialog.showModal();else if(!state.open&&dialog.open)dialog.close();},[state.open]);
    useEffect(()=>{const dialog=host('completion-dialog');const close=()=>{if(!(dialog as HTMLDialogElement).open&&completion.get().open)closeCompletion();};dialog.addEventListener('close',close);return()=>dialog.removeEventListener('close',close);},[]);
    return <><h1 id="completion-heading">Завершить разбор папки</h1><p id="completion-count">{state.preview?`Карточек: ${state.preview.cards}. Исходных публикаций: ${state.preview.originals}.`:'Подсчёт…'}</p><p>Удалятся тексты, медиа и сводки всей папки, включая скрытые материалы. Другие папки останутся.</p><button id="completion-confirm" disabled={isStopped()||state.busy||!state.preview?.originals} onClick={()=>void confirmCompletion()}>Подтвердить просмотр</button><button id="completion-close" onClick={closeCompletion}>Отмена</button><p id="completion-message" role="status">{state.message}</p></>;
  }
  mountSurface(host('digest-dialog'),<Digest/>);mountSurface(host('completion-dialog'),<Completion/>);
  return {
    openDigest(period:boolean){const folder=getFolder();if(!folder||isStopped())return;digest.set({open:true,folder,period,start:'',end:'',message:period?'Без дат используется настроенный период обзора.':'',busy:false,generation:digest.get().generation+1});},
    openCompletion,
    stop(){closeDigest();closeCompletion();},
  };
}
