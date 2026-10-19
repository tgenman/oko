import {createReadingCoordination} from './reading/coordination';
import {createMenus} from './settings/menus';
import {createDebugPanel, reportError} from './status/debug';
import {createTransport} from './api/transport';
import {createStatusPanel} from './status/panel';
import {createReaderActions} from './status/actions';
import {createReaderApi} from './api/reader';
import {initializeReaderShell,mountSurface,setRenderReporter} from './renderer';

import {useState} from 'react';
import {ReaderShell} from './shell';
import {errorMessage} from './api/errors';
import {regenerate,retryMaterial,retryTheses,retryFilter} from './api/generated/sdk.gen';
initializeReaderShell(<ReaderShell/>);
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const transport = createTransport({fetch:window.fetch.bind(window), token, origin:location.origin});
const readerApi = createReaderApi({fetch:window.fetch.bind(window), token, origin:location.origin,onError:(error,source)=>reportError(error,source,true)});
const debug = createDebugPanel({token, readerApi});
setRenderReporter(reportError);
const byId = (id:string):HTMLElement => {const node=document.getElementById(id);if(!node)throw new Error("Missing reader element: "+id);return node;};
const button=(id:string)=>byId(id) as HTMLButtonElement;
const progress=(text:string)=>{mountSurface(byId("progress"),<>{text}</>);};
let commandsAvailable = false;
const commandHosts = new Map<string,HTMLElement>();
let reading:ReturnType<typeof createReadingCoordination>;
let statusPanel:ReturnType<typeof createStatusPanel>;
const actions = createReaderActions({readerApi,onChanged:()=>statusPanel?.redraw()});
readerApi.setActionTracker(actions);
const api={discardDraft:async(path:string,field:string,method?:string)=>{await statusPanel.discardDraft(path,field,method);void reading.refresh();}};
const menus = createMenus({api, readerApi, onChanged: () => {void reading.changed();}});
statusPanel = createStatusPanel({debug, actions,resourceName:resource=>reading.resourceName(resource),onAvailability:available=>{commandsAvailable=available;renderCommands();}, onSettings:(target,trigger)=>menus.openStatus(target,trigger),
  onMaterial:id=>reading.openMaterial(id), onRetry:async(target)=>{
    if(target.kind==='retry_collection')await readerApi.collect(target.target);
    else if('material_ids'in target){
      for(const id of target.material_ids){const options={client:readerApi.client,path:{item:id}};
        if(target.kind==='retry_material')await readerApi.command({operation:'retry_material',material_id:id},headers=>retryMaterial({...options,headers}));
        else if(target.kind==='regenerate'&&target.result_kind==='theses')await readerApi.command({operation:'regenerate',material_id:id},headers=>regenerate({...options,headers}));
        else if(target.kind==='retry_theses')await readerApi.command({operation:'retry_theses',material_id:id},headers=>retryTheses({...options,headers}));
        else if(target.kind==='retry_filter'||target.kind==='regenerate'&&target.result_kind==='filter')await readerApi.command({operation:'retry_filter',material_id:id},headers=>retryFilter({...options,headers}));
      }
    }
    reading.refresh();
  }, onRefresh:()=>reading.refresh()});

reading = createReadingCoordination({readerApi, fetchMedia:transport.media, debug, menus, statusPanel, actions, progress,
  onStopped:()=>{button('stop').disabled=true;button('collect').disabled=true;button('folder-viewed').disabled=true;},
});

function Command({id,label,run,disabled}:{id:string;label:string;run:()=>Promise<unknown>;disabled:boolean}) {
  const [message,setMessage]=useState('');
  return <><button id={id} type="button" disabled={disabled} onClick={async()=>{setMessage('');try{await run();}catch(error){reportError(error,'Oko interface');setMessage(errorMessage(error));}}}><span className="reader-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{id==='collect'?<path d="M20 7v5h-5M4 17v-5h5M19 12a7 7 0 0 0-12-5L4 10m16 4-3 3a7 7 0 0 1-12-5"/>:<rect x="6" y="6" width="12" height="12" rx="2"/>}</svg></span>{label}</button><span className="header-command-error" role="status" hidden={!message}>{message}</span></>;
}
function renderCommands(){for(const [id,label,run] of [
  ['collect','Refresh Sources',async()=>{await readerApi.collect();await reading.refresh();}],
  ['stop','Stop',()=>reading.stop()],
] as const) {
  let host=commandHosts.get(id);
  if(!host){const control=byId(id);host=document.createElement('span');host.className='header-command';control.replaceWith(host);commandHosts.set(id,host);}
  mountSurface(host,<Command id={id} label={label} run={run} disabled={!commandsAvailable}/>);
}}
renderCommands();
document.querySelector('.reader-toolbar-scroll')!.addEventListener('focusin',event=>{const control=event.target instanceof Element?event.target.closest('button'):null;control?.scrollIntoView({block:'nearest',inline:'nearest'});});
reading.start();
