import type {CommandContext,Problem,ProblemSnapshot} from '../api/generated/types.gen';
import {observeCommand,commandReceipt,abandonProblem} from '../api/generated/sdk.gen';
import type {ReaderApi} from '../api/reader';
import {commandResources,isNavigation} from './recovery';
export type VisibleProblem=Problem&{saved?:boolean};
type Request={id:string;context:CommandContext;result:'unknown'|'failed';key:string;sequence:number};
export function createReaderActions({readerApi,onChanged}:{readerApi:ReaderApi;onChanged():void}) {
 const pending=new Map<string,{request:Request;problem:VisibleProblem}>(),latest=new Map<string,number>();
 let sequence=0,sending=false;
 function beginCommand(context:CommandContext):Request {
  const key=JSON.stringify(context),request:Request={id:crypto.randomUUID(),context,result:'unknown',key,sequence:++sequence};
  latest.set(key,request.sequence);return request;
 }
 function describe(request:Request,unknown:boolean):VisibleProblem {
  const resources=commandResources(request.context);
  // Unknown outcomes expose navigation only; commands are never replayed.
  const recovery=unknown?resources.recovery.filter(isNavigation):resources.recovery;
  return {id:'local-'+request.id,code:unknown?'command_outcome_unknown':'command_failed',command_id:request.id,
   title:unknown?'Action outcome unknown':'Action failed',message:unknown?'The action will not be retried automatically. Its result will be checked when the connection is restored.':'The draft stays in its original form.',
   consequence:'Check the action status.',affected:resources.affected,recovery,created_at:new Date().toISOString(),state:'current',saved:false};
 }
 async function flush(){if(sending||!pending.size)return;sending=true;try{for(const[key,entry]of pending){const{id,context,result}=entry.request;await observeCommand({client:readerApi.client,body:{id,context,result}});const receipt=(await commandReceipt({client:readerApi.client,path:{identity:id}})).data;if(pending.get(key)!==entry)continue;if(receipt.outcome==='succeeded'||receipt.outcome==='abandoned')pending.delete(key);else entry.problem.saved=true;}}catch{/* The visible local outcome remains until a later status response. */}finally{sending=false;onChanged();}}
 function matchesDraft(problem:Problem,path:string,field:string):boolean{return problem.recovery.some(intent=>intent.kind==='change_setting'?path==='settings/'+intent.target.scope+'/'+intent.target.owner&&intent.keys.some(key=>key===field):intent.kind==='configure_processor'?path==='processors/'+intent.processor_id:false);}
 return {
  beginCommand,flush,
  forgetMaterials(ids:string[]){const removed=new Set(ids);for(const[key,entry]of pending)if(entry.problem.affected.some(r=>r.kind==='material'&&removed.has(r.material_id)))pending.delete(key);},
  failed(request:Request|null,error:unknown){if(!request||latest.get(request.key)!==request.sequence)return;request.result=error&&typeof error==='object'&&'confirmed'in error&&error.confirmed?'failed':'unknown';pending.set(request.key,{request,problem:describe(request,request.result==='unknown')});onChanged();void flush();},
  succeeded(request:Request|null){if(request&&latest.get(request.key)===request.sequence){pending.delete(request.key);onChanged();}},
  merge(snapshot:ProblemSnapshot){const values=[...snapshot.current,...snapshot.recent];for(const[key,entry]of pending)if(values.some(p=>p.command_id===entry.request.id&&p.code!=='command_outcome_unknown'))pending.delete(key);const local=[...pending.values()].filter(entry=>!values.some(p=>p.command_id===entry.request.id)).map(entry=>entry.problem);const current=[...snapshot.current,...local];return {...snapshot,current,count:current.length};},
  async discard(path:string,field:string,snapshot:ProblemSnapshot,_method?:string){await flush();try{snapshot=(await readerApi.status()).problems;}catch{/* Preserve the last observed outcome. */}const failed=snapshot.current.find(p=>p.code==='command_failed'&&matchesDraft(p,path,field));if(failed)await abandonProblem({client:readerApi.client,path:{identity:failed.id}});for(const[key,entry]of pending)if(entry.problem.command_id===failed?.command_id||entry.request.result==='failed'&&matchesDraft(entry.problem,path,field))pending.delete(key);onChanged();},
 };
}
export type ReaderActions=ReturnType<typeof createReaderActions>;
