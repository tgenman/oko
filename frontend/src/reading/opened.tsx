import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {material as readMaterial,previousVersion,regenerate,retryMaterial,retryTheses,retryFilter,restoreFilter,viewed} from '../api/generated/sdk.gen';
import type {Card,Material,Version,Previous,Point,Attachment} from '../api/generated/types.gen';
import type {ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import {createStore,useStore,host,errorMessage} from './store';

type MediaFetch=(path:string,signal?:AbortSignal,cache?:RequestCache)=>Promise<Response>;
type OpenCard=Pick<Card,'id'>&Partial<Card>;
type Session={id:string;folder_id:string|undefined;cardKey:string;controller:AbortController};
type Resource={url:string;kind:Attachment['kind'];name:string};
function Media({material,fetchMedia,session,id,className}:{material:Pick<Material,'images'|'attachments'>;fetchMedia:MediaFetch;session:Session;id?:string;className?:string}) {
  const ref=useRef<HTMLDivElement>(null),[resources,setResources]=useState<Resource[]>([]);
  useLayoutEffect(()=>()=>{for(const media of ref.current?.querySelectorAll<HTMLMediaElement>('video,audio')||[]){media.pause();media.removeAttribute('src');media.load();}},[]);
  useEffect(()=>{
    let active=true;const urls:string[]=[];
    void(async()=>{try{
      const inputs=[...(material.images||[]).map(url=>({url,kind:'image' as const})),...(material.attachments||[])];
      for(const input of inputs){if(!active||session.controller.signal.aborted)return;const response=await fetchMedia(input.url,session.controller.signal);if(!response.ok)continue;const blob=await response.blob();if(!active||session.controller.signal.aborted)return;const url=URL.createObjectURL(blob);urls.push(url);setResources(previous=>[...previous,{url,kind:input.kind,name:input.url.split('/').pop()||''}]);}
    }catch(error){if(active&&!(error instanceof Error&&error.name==='AbortError'))reportError(error,'Открытый материал');}})();
    return()=>{active=false;for(const url of urls)URL.revokeObjectURL(url);};
  },[material,session,fetchMedia]);
  return <div ref={ref} {...(id?{id}:{})} {...(className?{className}:{})}>{resources.map(resource=>resource.kind==='image'?<img key={resource.url} src={resource.url} alt="Изображение публикации"/>:resource.kind==='video'||resource.kind==='circle'?<video key={resource.url} src={resource.url} controls preload="metadata" className={resource.kind==='circle'?'circle':undefined} aria-label={resource.kind==='circle'?'Кружочек Telegram':'Видео публикации'}/>:resource.kind==='audio'?<audio key={resource.url} src={resource.url} controls preload="metadata" aria-label="Аудио публикации"/>:<a key={resource.url} href={resource.url} download={resource.name}>Скачать вложение</a>)}</div>;
}
function PreviousVersion({previous,session,readerApi,fetchMedia}:{previous:Previous;session:Session;readerApi:ReaderApi;fetchMedia:MediaFetch}) {
  const [version,setVersion]=useState<Version|null>(null),[error,setError]=useState(''),active=useRef(true),loading=useRef(false);
  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  async function load() {
    if(loading.current||version)return;loading.current=true;setError('');
    try {
      const url=new URL(previous.url,location.origin),parts=url.pathname.split('/');
      const number=previous.number??Number(parts.at(-1));
      if(url.origin!==location.origin||parts[1]!=='api'||parts[2]!=='materials'||parts[4]!=='versions'||!Number.isInteger(number))throw new Error('Некорректная ссылка предыдущей версии');
      const reply=await previousVersion({client:readerApi.client,path:{item:decodeURIComponent(parts[3]!),number},signal:session.controller.signal});
      if(active.current&&!session.controller.signal.aborted)setVersion(reply.data);
    }catch(error){reportError(error,'Предыдущая версия');if(active.current&&!session.controller.signal.aborted){setError(errorMessage(error));loading.current=false;}}
  }
  return <details onToggle={event=>{if(event.currentTarget.open)void load();}}><summary>Предыдущая версия</summary><div>{version?<><h3>{version.title}</h3><p>{version.text}</p><Media material={version} fetchMedia={fetchMedia} session={session}/></>:error}</div></details>;
}
function Versions({material,session,readerApi,fetchMedia}:{material:Pick<Material,'previous'>;session:Session;readerApi:ReaderApi;fetchMedia:MediaFetch}) {return <>{(material.previous||[]).map(previous=><PreviousVersion key={previous.url} previous={previous} session={session} readerApi={readerApi} fetchMedia={fetchMedia}/>)}</>;}
function Points({points}:{points:Point[]}) {return <>{points.map((point,index)=><li key={index}>{point.text}{point.sources.map((ref,index)=><a key={index} href={ref.url} target="_blank" rel="noopener noreferrer"> Источник</a>)}</li>)}</>;}

export function createOpenedMaterial({readerApi,fetchMedia,onViewed,onChanged}:{readerApi:ReaderApi;fetchMedia:MediaFetch;onViewed:(id:string)=>void;onChanged:(changed:boolean)=>void}) {
  const store=createStore({session:null as Session|null,material:null as Material|null,empty:'Выберите материал',message:'',originalOpen:true,processingOpen:false});
  const active=(session:Session)=>store.get().session===session&&!session.controller.signal.aborted;
  function clear(empty='Выберите материал') {store.get().session?.controller.abort();store.set({session:null,material:null,empty,message:'',originalOpen:true,processingOpen:false});}
  async function open(card:OpenCard) {
    clear('Загрузка материала…');
    const session:Session={id:card.id,folder_id:card.folder_id,cardKey:JSON.stringify(card),controller:new AbortController()};
    store.set({...store.get(),session});
    try {const material=(await readMaterial({client:readerApi.client,path:{item:card.id},signal:session.controller.signal})).data;if(!active(session))return;const filter=material.filter;store.set({...store.get(),material,originalOpen:material.originals!==false&&(!filter||filter.status!=='done'||filter.restored||!filter.removed.length&&!filter.hidden)});}catch(error){reportError(error,'Открытый материал');if(active(session))clear(errorMessage(error));}
  }
  const operations={regenerate,retryMaterial,retryTheses,retryFilter,restoreFilter};
  async function action(kind:keyof typeof operations,changed=false) {const session=store.get().session;if(!session||!store.get().material)return;try{await operations[kind]({client:readerApi.client,path:{item:session.id}});if(active(session))onChanged(changed);}catch(error){reportError(error,'Открытый материал');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  async function copy(url:string) {const session=store.get().session;if(!session)return;try{await navigator.clipboard.writeText(url);if(active(session))store.set({...store.get(),message:'Ссылка скопирована'});}catch(error){reportError(error,'Открытый материал');if(active(session))store.set({...store.get(),message:'Браузер не разрешил копирование'});}}
  async function markViewed() {const session=store.get().session;if(!session||!store.get().material)return;try{await viewed({client:readerApi.client,path:{item:session.id}});if(active(session))clear();onViewed(session.id);}catch(error){reportError(error,'Открытый материал');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  function Surface() {
    const state=useStore(store),material=state.material,session=state.session,filter=material?.filter,hasResult=!!material?.theses,origin=material?.provenance;
    const provenance=!origin?.model?'Модель неизвестна':[origin.processor,origin.model,'reasoning_effort'in origin?origin.reasoning_effort:'','fast'in origin?(origin.fast?'Fast включён':'Fast выключен'):''].filter(Boolean).join(' · ');
    const completeness=!material?'':!material.complete?'Загрузка материала':material.loading?(material.captions?'Получение субтитров и превью':'Текст сохранён · загрузка вложений'):material.captions?(material.reason||'Субтитры и превью сохранены'):material.full===false?'Неполный материал · '+material.reason:'Полностью сохранено';
    const filterStatus=!filter||filter.status==='off'?'':filter.status==='done'?(filter.restored?'Возвращено пользователем · ':filter.hidden?'Скрыто · ':filter.removed.length?'Рекламные вставки убраны · ':'Фильтрация завершена')+(filter.reason||''):filter.error||'Ожидание фильтрации';
    return <><div id="empty" hidden={!!material}>{state.empty}</div><div id="material" hidden={!material}><p className="meta" id="completeness">{completeness}{material?.changed?' · Изменено':''}{material?.source_deleted?' · Недоступно в источнике; локальная копия сохранена':''}</p><h1 id="title">{material?.url?<a href={material.url} target="_blank" rel="noopener noreferrer">{material.title}</a>:material?.title||''}</h1><p id="date">{material?(material.published_known===false?'Дата неизвестна':new Date(material.published*1000).toLocaleString('ru')):''}</p><div id="theses-panel" hidden={!material?.ai&&!material?.theses}><h2>Тезисы публикации</h2><p id="ai-status" role="status">{!material?'':material.theses?'Тезисы готовы':material.ai?.error||(material.ai?.status==='running'?'Подготовка тезисов':'Ожидание обработчика')}</p><ul id="theses"><Points points={material?.theses?.points||[]}/></ul><button id="theses-retry" hidden={!material?.ai?.error} onClick={()=>void action('retryTheses')}>Повторить подготовку тезисов</button></div><div id="filter-panel" hidden={!filter||filter.status==='off'}><p id="filter-status" role="status">{filterStatus}</p><button id="filter-restore" hidden={!filter||filter.status!=='done'||filter.restored||!(filter.hidden||filter.removed.length)} onClick={()=>void action('restoreFilter',true)}>Вернуть материал</button><button id="filter-retry" hidden={!filter?.error} onClick={()=>void action('retryFilter')}>Повторить фильтрацию</button><div id="filtered-text">{filter?.status==='done'&&!filter.hidden&&!filter.restored&&filter.removed.length?material?.display_text:''}</div></div><details id="original-panel" open={state.originalOpen} onToggle={event=>{if(event.currentTarget.open!==store.get().originalOpen)store.set({...store.get(),originalOpen:event.currentTarget.open});}}><summary>Оригинал публикации</summary><div id="description-panel" hidden={!material?.description}><h3>Описание видео</h3><div id="description">{material?.description||''}</div></div><div id="text">{material?.text||''}</div>{material&&session?<Media key={session.id+'/'+session.cardKey} id="images" material={material} session={session} fetchMedia={fetchMedia}/>:<div id="images"/>}</details><div id="versions">{material&&session&&<Versions key={session.id+'/'+session.cardKey} material={material} session={session} readerApi={readerApi} fetchMedia={fetchMedia}/>}</div><details id="processing-details" hidden={!hasResult} open={state.processingOpen} onToggle={event=>{if(event.currentTarget.open!==store.get().processingOpen)store.set({...store.get(),processingOpen:event.currentTarget.open});}}><summary>Сведения об обработке</summary><p id="processing-provenance">{hasResult?provenance:''}</p></details><button id="regenerate" hidden={!hasResult} onClick={()=>void action('regenerate')}>Пересоздать</button><div className="actions"><button id="retry" hidden={!material?.complete||material.full!==false||!!material.loading} onClick={()=>void action('retryMaterial')}>Повторить загрузку</button><button id="copy" onClick={()=>material&&void copy(material.url)}>Скопировать ссылку на оригинал</button><button id="viewed" onClick={()=>void markViewed()}>Просмотрено</button></div><p id="message" role="status">{state.message}</p></div></>;
  }
  mountSurface(host('material').parentElement!,<Surface/>);
  return {
    get selection(){const session=store.get().session;return session?{id:session.id,folder_id:session.folder_id}:null;},open,clear,
    reconcile(cards:Card[]){const session=store.get().session;if(!session)return;const card=cards.find(card=>card.id===session.id);if(!card)clear();else if(JSON.stringify(card)!==session.cardKey)void open(card);},
    viewed(id:string){if(store.get().session?.id===id)clear();},
    folderCompleted(folder:string){if(store.get().session?.folder_id===folder)clear();},
  };
}
