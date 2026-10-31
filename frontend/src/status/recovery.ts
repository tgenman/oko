import type {AffectedResource,CommandContext,Problem,RecoveryIntent,RuntimeStatus} from '../api/generated/types.gen';
export type Section='action'|'wait'|'object';
const presentation:Record<Problem['code'],{section:Section;title:string}>={
 job_target_unknown:{section:'object',title:'Saved Job Needs Review'},
 processor_not_configured:{section:'action',title:'No Model Selected'},
 processor_configuration_invalid:{section:'action',title:'Check Model Settings'},
 public_ai_not_allowed:{section:'action',title:'Processing Not Allowed'},
 source_reconnect_required:{section:'action',title:'Check Source Access'},
 source_unavailable:{section:'object',title:'Source Temporarily Unavailable'},
 material_incomplete:{section:'object',title:'Item Not Fully Saved'},
 processing_failed:{section:'object',title:'Processing Failed'},
 youtube_rate_limited:{section:'wait',title:'YouTube Rate Limit Reached'},
 storage_unavailable:{section:'action',title:'Storage unavailable'},
 command_outcome_unknown:{section:'wait',title:'Action outcome unknown'},
 command_failed:{section:'action',title:'Action failed'},
 material_save_failed:{section:'object',title:'Could Not Save Item'},
 material_read_failed:{section:'object',title:'Could Not Read Local Item'},
 feed_delete_failed:{section:'object',title:'Deletion Incomplete'},
 telegram_read_failed:{section:'object',title:'Could Not Acknowledge Read Status'},
 profile_not_configured:{section:'action',title:'Configure Telegram Profile'},
 profile_unavailable:{section:'action',title:'Check Telegram Profile'},
 account_identity_mismatch:{section:'action',title:'Profile Points to Another Account'},
 source_identity_conflict:{section:'action',title:'Source Binding Conflict'},
 duplicate_source:{section:'action',title:'Source Already Connected'},
 profile_in_use:{section:'action',title:'Cannot Replace an Account in Use'},
 profile_account_conflict:{section:'action',title:'Same Account Assigned to Both Roles'},
};
export const section=(problem:Problem):Section=>presentation[problem.code]?.section||'object';
export const problemTitle=(problem:Problem)=>problem.code==='command_failed'&&problem.message.trim()?problem.message:problem.code==='processor_not_configured'&&problem.affected.some(r=>r.kind==='processor'&&r.processor_id==='codex')?'No Codex Model Selected':presentation[problem.code]?.title||problem.title;
export function problemConsequence(problem:Problem):string {
 switch(problem.code){
 case 'processor_not_configured':
  return problem.affected.some(r=>r.kind==='processor'&&r.processor_id==='codex')?'Summaries are not being generated yet.':problem.consequence;
 case 'storage_unavailable':return 'Collection and processing are stopped.';
 case 'material_incomplete':return 'The available portion can still be read.';
 case 'feed_delete_failed':return 'Oko continues trying to delete the data.';
 case 'command_outcome_unknown':return 'saved' in problem&&problem.saved===false?problem.consequence:'Oko is checking the result; the action will not be retried automatically.';
 default:return problem.consequence;
 }
}
export const isNavigation=(intent:RecoveryIntent)=>['configure_processor','configure_telegram_profile','change_setting','connect_source','inspect_materials'].includes(intent.kind);
export function recoveryLabel(intent:RecoveryIntent):string {
 switch(intent.kind){
 case 'configure_processor':return 'Configure Model';
 case 'configure_telegram_profile':return 'Configure Telegram Profile';
 case 'change_setting':return 'Open Settings';
 case 'connect_source':return 'Add Source';
 case 'inspect_materials':return 'Show Items';
 case 'retry_material':return 'Retry Download';
 case 'retry_theses':return 'Retry Processing';
 case 'retry_filter':return 'Retry Filtering';
 case 'regenerate':return 'Regenerate Result';
 case 'retry_collection':return intent.target.scope==='feed'?'Refresh Source':intent.target.scope==='folder'?'Refresh Folder':'Refresh Sources';
 }
}
export function workText(state:RuntimeStatus):string {
 if(state.runtime_state==='disabled')return 'Oko is off';
 if(state.runtime_state==='stopped')return 'Oko stopped';
 if(state.runtime_state==='unavailable')return 'Storage unavailable';
 const labels=state.work.flatMap(work=>{switch(work.phase){case 'idle':return [];case 'collecting':return ['Collecting Sources'];case 'deleting':return ['Local Cleanup'];case 'processing':return [work.operation==='filter'?'Filtering':'Generating summary'];case 'waiting':return ['Waiting'];}});
 return [...new Set(labels)].join(' · ')||'Done';
}
export function commandResources(context:CommandContext):{affected:AffectedResource[];recovery:RecoveryIntent[]} {
 if(context.operation==='settings_patch')return {affected:[{kind:'settings',target:context.target}],recovery:context.keys.length?[{kind:'change_setting',target:context.target,keys:context.keys}]:[]};
 if(context.operation==='processor_save'||context.operation==='processor_check'||context.operation==='codex_save'||context.operation==='codex_check'){const processor_id=context.operation.startsWith('codex')?'codex':'llamacpp';return {affected:[{kind:'processor',processor_id}],recovery:[{kind:'configure_processor',processor_id}]};}
 if(context.operation==='telegram_profile_save')return {affected:[{kind:'telegram_profile',role:context.role}],recovery:[{kind:'configure_telegram_profile',role:context.role}]};
 if(context.operation==='telegram_connect')return {affected:[],recovery:[{kind:'connect_source',source_kind:'telegram'}]};
 if(context.operation==='add_feed'||context.operation==='verify_feed')return {affected:[],recovery:[{kind:'connect_source',source_kind:'rss'}]};
 if('material_id'in context)return {affected:[{kind:'material',material_id:context.material_id}],recovery:[{kind:'inspect_materials',material_ids:[context.material_id]}]};
 if('feed_id'in context)return {affected:[{kind:'feed',feed_id:context.feed_id}],recovery:[]};
 if('folder_id'in context)return {affected:[{kind:'folder',folder_id:context.folder_id}],recovery:[]};
 if(context.operation==='collect')return {affected:[],recovery:[{kind:'retry_collection',target:context.target}]};
 return {affected:[{kind:'runtime'}],recovery:[]};
}
