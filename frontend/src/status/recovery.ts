import type {AffectedResource,CommandContext,Problem,RecoveryIntent,RuntimeStatus} from '../api/generated/types.gen';
export type Section='action'|'wait'|'object';
const presentation:Record<Problem['code'],{section:Section;title:string}>={
 processor_not_configured:{section:'action',title:'Не выбрана модель'},
 processor_configuration_invalid:{section:'action',title:'Проверьте настройки модели'},
 public_ai_not_allowed:{section:'action',title:'Обработка не разрешена'},
 source_reconnect_required:{section:'action',title:'Проверьте доступ к источнику'},
 source_unavailable:{section:'object',title:'Источник временно недоступен'},
 material_incomplete:{section:'object',title:'Материал сохранён не полностью'},
 processing_failed:{section:'object',title:'Не удалось обработать материал'},
 youtube_rate_limited:{section:'wait',title:'YouTube ограничил запросы'},
 storage_unavailable:{section:'action',title:'Хранилище недоступно'},
 command_outcome_unknown:{section:'wait',title:'Результат действия пока неизвестен'},
 command_failed:{section:'action',title:'Не удалось выполнить действие'},
 material_save_failed:{section:'object',title:'Не удалось сохранить материал'},
 material_read_failed:{section:'object',title:'Не удалось прочитать локальный материал'},
 feed_delete_failed:{section:'object',title:'Удаление не завершено'},
 telegram_read_failed:{section:'object',title:'Не удалось подтвердить прочитанное'},
 profile_not_configured:{section:'action',title:'Настройте профиль Telegram'},
 profile_unavailable:{section:'action',title:'Проверьте профиль Telegram'},
 account_identity_mismatch:{section:'action',title:'Профиль указывает на другой аккаунт'},
 source_identity_conflict:{section:'action',title:'Конфликт привязки источника'},
 duplicate_source:{section:'action',title:'Источник уже подключён'},
 profile_in_use:{section:'action',title:'Аккаунт занятого профиля нельзя заменить'},
 profile_account_conflict:{section:'action',title:'Один аккаунт указан для двух ролей'},
 legacy_problem:{section:'object',title:'Сохранённая проблема'},
};
export const section=(problem:Problem):Section=>presentation[problem.code]?.section||'object';
export const problemTitle=(problem:Problem)=>problem.code==='legacy_problem'?problem.title||presentation.legacy_problem.title:problem.code==='processor_not_configured'&&problem.affected.some(r=>r.kind==='processor'&&r.processor_id==='codex')?'Не выбрана модель Codex':presentation[problem.code]?.title||problem.title;
export const isNavigation=(intent:RecoveryIntent)=>['configure_processor','configure_telegram_profile','change_setting','connect_source','inspect_materials'].includes(intent.kind);
export function recoveryLabel(intent:RecoveryIntent):string {
 switch(intent.kind){
 case 'configure_processor':return 'Настроить модель';
 case 'configure_telegram_profile':return 'Настроить профиль Telegram';
 case 'change_setting':return 'Открыть настройки';
 case 'connect_source':return 'Добавить источник';
 case 'inspect_materials':return 'Показать материалы';
 case 'retry_material':return 'Повторить загрузку';
 case 'retry_theses':return 'Повторить обработку';
 case 'retry_filter':return 'Повторить фильтрацию';
 case 'regenerate':return 'Пересоздать результат';
 case 'retry_collection':return intent.target.scope==='feed'?'Обновить источник':'Обновить источники';
 }
}
export function workText(state:RuntimeStatus):string {
 if(state.runtime_state==='stopped')return 'Око остановлено';
 if(state.runtime_state==='unavailable')return 'Хранилище недоступно';
 const labels=state.work.flatMap(work=>{switch(work.phase){case 'idle':return [];case 'collecting':return ['Сбор источников'];case 'deleting':return ['Локальная очистка'];case 'processing':return [work.operation==='filter'?'Фильтрация':'Подготовка тезисов'];case 'waiting':return ['Ожидание'];}});
 return [...new Set(labels)].join(' · ')||'Готово';
}
export function commandResources(context:CommandContext):{affected:AffectedResource[];recovery:RecoveryIntent[]} {
 if(context.operation==='settings_patch')return {affected:[{kind:'settings',target:context.target}],recovery:context.keys.length?[{kind:'change_setting',target:context.target,keys:context.keys}]:[]};
 if(context.operation==='processor_save'||context.operation==='processor_check'||context.operation==='codex_save'||context.operation==='codex_check'){const processor_id=context.operation.startsWith('codex')?'codex':'llamacpp';return {affected:[{kind:'processor',processor_id}],recovery:[{kind:'configure_processor',processor_id}]};}
 if(context.operation==='telegram_profile_save')return {affected:[{kind:'telegram_profile',role:context.role}],recovery:[{kind:'configure_telegram_profile',role:context.role}]};
 if(context.operation==='telegram_connect')return {affected:[],recovery:[{kind:'connect_source',source_kind:'telegram'}]};
 if(context.operation==='add_feed'||context.operation==='verify_feed')return {affected:[],recovery:[{kind:'connect_source',source_kind:'rss'}]};
 if('material_id'in context)return {affected:[{kind:'material',material_id:context.material_id}],recovery:[{kind:'inspect_materials',material_ids:[context.material_id]}]};
 if('feed_id'in context)return {affected:[{kind:'feed',feed_id:context.feed_id}],recovery:[]};
 if(context.operation==='complete_folder')return {affected:[{kind:'folder',folder_id:context.folder_id}],recovery:[]};
 if(context.operation==='collect')return {affected:[],recovery:[{kind:'retry_collection',target:context.target}]};
 return {affected:[{kind:'runtime'}],recovery:[]};
}
