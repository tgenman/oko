import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {material as readMaterial,previousVersion,regenerate,retryMaterial,retryTheses,retryFilter,restoreFilter,viewed} from '../api/generated/sdk.gen';
import type {ReadingCard as Card,ReadableMaterial as Material,PublicationVersion as Version,PreviousVersionReference as Previous,ThesesPoint as Point,Attachment,AvailablePublicationContent} from '../api/generated/types.gen';
import type {ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import {createStore,useStore,host,errorMessage} from './store';

type MediaFetch=(path:string,signal?:AbortSignal,cache?:RequestCache)=>Promise<Response>;
type OpenCard=Pick<Card,'id'>&Partial<Card>;
type Session={id:string;folder_id:string|undefined;cardKey:string;sequence:number;controller:AbortController};
type Resource={url:string;kind:Attachment['kind'];name:string};
function Media({material,fetchMedia,session,id,className}:{material:Pick<AvailablePublicationContent,'images'|'attachments'>;fetchMedia:MediaFetch;session:Session;id?:string;className?:string}) {
  const ref=useRef<HTMLDivElement>(null),[resources,setResources]=useState<Resource[]>([]);
  useLayoutEffect(()=>()=>{for(const media of ref.current?.querySelectorAll<HTMLMediaElement>('video,audio')||[]){media.pause();media.removeAttribute('src');media.load();}},[]);
  const resourcesKey=JSON.stringify([material.images,material.attachments]);
  useEffect(()=>{
    let active=true;const urls:string[]=[];setResources([]);
    void(async()=>{try{
      const inputs=[...(material.images||[]).map(url=>({url,kind:'image' as const})),...(material.attachments||[])];
      for(const input of inputs){if(!active||session.controller.signal.aborted)return;const response=await fetchMedia(input.url,session.controller.signal);if(!response.ok)continue;const blob=await response.blob();if(!active||session.controller.signal.aborted)return;const url=URL.createObjectURL(blob);urls.push(url);setResources(previous=>[...previous,{url,kind:input.kind,name:input.url.split('/').pop()||''}]);}
    }catch(error){if(active&&!(error instanceof Error&&error.name==='AbortError'))reportError(error,'Открытый материал');}})();
    return()=>{active=false;for(const url of urls)URL.revokeObjectURL(url);};
  },[resourcesKey,session,fetchMedia]);
  return <div ref={ref} {...(id?{id}:{})} {...(className?{className}:{})}>{resources.map(resource=>resource.kind==='image'?<img key={resource.url} src={resource.url} alt="Изображение публикации"/>:resource.kind==='video'||resource.kind==='circle'?<video key={resource.url} src={resource.url} controls preload="metadata" className={resource.kind==='circle'?'circle':undefined} aria-label={resource.kind==='circle'?'Кружочек Telegram':'Видео публикации'}/>:resource.kind==='audio'?<audio key={resource.url} src={resource.url} controls preload="metadata" aria-label="Аудио публикации"/>:<a key={resource.url} href={resource.url} download={resource.name}>Скачать вложение</a>)}</div>;
}
function PreviousVersion({previous,session,readerApi,fetchMedia}:{previous:Previous;session:Session;readerApi:ReaderApi;fetchMedia:MediaFetch}) {
  const [version,setVersion]=useState<Version|null>(null),[error,setError]=useState(''),active=useRef(true),loading=useRef(false);
  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  async function load() {
    if(loading.current||version)return;loading.current=true;setError('');
    try {
      const number=previous.number;
      const reply=await previousVersion({client:readerApi.client,path:{item:session.id,number},signal:session.controller.signal});
      if(active.current&&!session.controller.signal.aborted)setVersion(reply.data);
    }catch(error){reportError(error,'Предыдущая версия');if(active.current&&!session.controller.signal.aborted){setError(errorMessage(error));loading.current=false;}}
  }
  return <details onToggle={event=>{if(event.currentTarget.open)void load();}}><summary>Предыдущая версия</summary><div>{version?<><h3>{version.title}</h3><p>{version.text}</p><Media material={version} fetchMedia={fetchMedia} session={session}/></>:error}</div></details>;
}
function Versions({material,session,readerApi,fetchMedia}:{material:Pick<AvailablePublicationContent,'previous_versions'>;session:Session;readerApi:ReaderApi;fetchMedia:MediaFetch}) {return <>{(material.previous_versions).map(previous=><PreviousVersion key={previous.url} previous={previous} session={session} readerApi={readerApi} fetchMedia={fetchMedia}/>)}</>;}
function Points({points}:{points:Point[]}) {return <>{points.map((point,index)=><li key={index}>{point.text}{point.references.map((ref,index)=><a key={index} href={ref.source_url} target="_blank" rel="noopener noreferrer"> Источник</a>)}</li>)}</>;}

function availableContent(material:Material):AvailablePublicationContent|null {
  switch(material.availability) {
    case 'available': return material.content;
    case 'pending': return null;
    default: {const unreachable:never=material;return unreachable;}
  }
}

export function createOpenedMaterial({readerApi,fetchMedia,onViewed,onChanged}:{readerApi:ReaderApi;fetchMedia:MediaFetch;onViewed:(id:string)=>void;onChanged:(changed:boolean)=>void}) {
  const store=createStore({session:null as Session|null,material:null as Material|null,empty:'Выберите материал',message:'',originalOpen:true,processingOpen:false});
  const active=(session:Session)=>store.get().session===session&&!session.controller.signal.aborted;
  function clear(empty='Выберите материал') {store.get().session?.controller.abort();store.set({session:null,material:null,empty,message:'',originalOpen:true,processingOpen:false});}
  async function open(card:OpenCard,refresh=false) {
    if(!refresh)clear('Загрузка материала…');
    const session=refresh?store.get().session:{id:card.id,folder_id:card.folder_id,cardKey:JSON.stringify(card),sequence:0,controller:new AbortController()};
    if(!session)return;
    session.cardKey=JSON.stringify(card);const sequence=++session.sequence;
    if(!refresh)store.set({...store.get(),session});
    try {
      const material=(await readMaterial({client:readerApi.client,path:{item:card.id},signal:session.controller.signal})).data;
      if(!active(session)||sequence!==session.sequence)return;
      const filter=material.filter.result;
      store.set({...store.get(),material,...(!refresh?{originalOpen:material.originals&&(!filter||filter.restored||!filter.removed.length&&!filter.hidden)}:{})});
    }catch(error){
      if(!active(session)||sequence!==session.sequence)return;
      reportError(error,'Открытый материал');
      if(refresh)store.set({...store.get(),message:errorMessage(error)});else clear(errorMessage(error));
    }
  }
  const operations={regenerate,retryMaterial,retryTheses,retryFilter,restoreFilter};
  const intents={regenerate:'regenerate',retryMaterial:'retry_material',retryTheses:'retry_theses',retryFilter:'retry_filter',restoreFilter:'restore_filter'} as const;
  async function action(kind:keyof typeof operations,changed=false) {const session=store.get().session;if(!session||!store.get().material)return;try{await readerApi.command({operation:intents[kind],material_id:session.id},headers=>operations[kind]({client:readerApi.client,headers,path:{item:session.id}}));if(active(session))onChanged(changed);}catch(error){reportError(error,'Открытый материал');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  async function copy(url:string) {const session=store.get().session;if(!session)return;try{await navigator.clipboard.writeText(url);if(active(session))store.set({...store.get(),message:'Ссылка скопирована'});}catch(error){reportError(error,'Открытый материал');if(active(session))store.set({...store.get(),message:'Браузер не разрешил копирование'});}}
  async function markViewed() {const session=store.get().session;if(!session||!store.get().material)return;try{await readerApi.command({operation:'viewed',material_id:session.id},headers=>viewed({client:readerApi.client,headers,path:{item:session.id}}));if(active(session))clear();onViewed(session.id);}catch(error){reportError(error,'Открытый материал');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  function Surface() {
    const state=useStore(store),material=state.material,session=state.session;
    const content=material?availableContent(material):null,filter=material?.filter.result,theses=material?.theses.result,hasResult=!!theses,origin=theses?.provenance;
    const provenance=!origin||origin.kind==='unknown'?'Модель неизвестна':origin.kind==='codex'?[origin.kind,origin.model,origin.reasoning_effort,origin.fast?'Fast включён':'Fast выключен'].join(' · '):[origin.processor_id,origin.model.state==='known'?origin.model.name:'Модель неизвестна'].join(' · ');
    const captions=material?.source.kind==='youtube';
    const completeness=!material?'':!content?'Загрузка материала':content.loading?(captions?'Получение субтитров и превью':'Текст сохранён · загрузка вложений'):content.completeness.state==='incomplete'?'Неполный материал · '+content.completeness.message:captions?'Субтитры и превью сохранены':'Полностью сохранено';
    const filterStatus=filter?(filter.restored?'Возвращено пользователем · ':filter.hidden?'Скрыто · ':filter.removed.length?'Рекламные вставки убраны · ':'Фильтрация завершена')+filter.reason:material?.filter.diagnostic_message||'Ожидание фильтрации';
    return <><div id="empty" hidden={!!material}>{state.empty}</div><div id="material" hidden={!material}><p className="meta" id="completeness">{completeness}{content?.changed?' · Изменено':''}{content?.source_deleted?' · Недоступно в источнике; локальная копия сохранена':''}</p><h1 id="title">{material?.source_url?<a href={material.source_url} target="_blank" rel="noopener noreferrer">{material.title}</a>:material?.title||''}</h1><p id="date">{material?(material.published_at===null?'Дата неизвестна':new Date(material.published_at!).toLocaleString('ru')):''}</p><div id="theses-panel" hidden={!theses&&material?.theses.attempt.state==='disabled'}><h2>Тезисы публикации</h2><p id="ai-status" role="status">{!material?'':theses?'Тезисы готовы':material.theses.diagnostic_message||(material.theses.attempt.state==='running'?'Подготовка тезисов':'Ожидание обработчика')}</p><ul id="theses"><Points points={theses?.points||[]}/></ul><button id="theses-retry" hidden={!material?.theses.diagnostic_message} onClick={()=>void action('retryTheses')}>Повторить подготовку тезисов</button></div><div id="filter-panel" hidden={material?.filter.attempt.state==='disabled'}><p id="filter-status" role="status">{filterStatus}</p><button id="filter-restore" hidden={!filter||filter.restored||!(filter.hidden||filter.removed.length)} onClick={()=>void action('restoreFilter',true)}>Вернуть материал</button><button id="filter-retry" hidden={!material?.filter.diagnostic_message} onClick={()=>void action('retryFilter')}>Повторить фильтрацию</button><div id="filtered-text">{filter&&!filter.hidden&&!filter.restored&&filter.removed.length?content?.display_text:''}</div></div><details id="original-panel" open={state.originalOpen} onToggle={event=>{if(event.currentTarget.open!==store.get().originalOpen)store.set({...store.get(),originalOpen:event.currentTarget.open});}}><summary>Оригинал публикации</summary><div id="description-panel" hidden={!content?.description}><h3>Описание видео</h3><div id="description">{content?.description||''}</div></div><div id="text">{content?.text||''}</div>{content&&session?<Media key={session.id+'/'+content.revision_id} id="images" material={content} session={session} fetchMedia={fetchMedia}/>:<div id="images"/>}</details><div id="versions">{content&&session&&<Versions key={session.id+'/'+content.revision_id} material={content} session={session} readerApi={readerApi} fetchMedia={fetchMedia}/>}</div><details id="processing-details" hidden={!hasResult} open={state.processingOpen} onToggle={event=>{if(event.currentTarget.open!==store.get().processingOpen)store.set({...store.get(),processingOpen:event.currentTarget.open});}}><summary>Сведения об обработке</summary><p id="processing-provenance">{hasResult?provenance:''}</p></details><button id="regenerate" hidden={!hasResult} onClick={()=>void action('regenerate')}>Пересоздать</button><div className="actions"><button id="retry" hidden={!content||content.completeness.state!=='incomplete'||content.loading} onClick={()=>void action('retryMaterial')}>Повторить загрузку</button><button id="copy" onClick={()=>material&&void copy(material.source_url)}>Скопировать ссылку на оригинал</button><button id="viewed" onClick={()=>void markViewed()}>Просмотрено</button></div><p id="message" role="status">{state.message}</p></div></>;
  }
  mountSurface(host('material').parentElement!,<Surface/>);
  return {
    get selection(){const session=store.get().session;return session?{id:session.id,folder_id:session.folder_id}:null;},open,clear,
    reconcile(cards:Card[]){const session=store.get().session;if(!session)return;const card=cards.find(card=>card.id===session.id);if(!card)clear();else if(JSON.stringify(card)!==session.cardKey)void open(card,true);},
    viewed(id:string){if(store.get().session?.id===id)clear();},
    folderCompleted(folder:string){if(store.get().session?.folder_id===folder)clear();},
  };
}
