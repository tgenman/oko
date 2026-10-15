import type {CommandContext} from '../api/generated/types.gen';
import {observeCommand,commandReceipt,abandonProblem} from '../api/generated/sdk.gen';
import type { ReaderApi } from '../api/reader';
import type { Problem, ProblemAction, ProblemObject, ProblemSnapshot, ReportAction } from '../api/legacy-generated/types.gen';
import { reportReaderAction } from '../api/legacy-generated/sdk.gen';
export type VisibleProblem = Problem & {
    saved?: boolean;
};
type Request = ReportAction & {
    context?:CommandContext;
    fields: string[];
    sequence: number;
    key: string;
};
type Failure = {
    name?: string;
    confirmed?: boolean;
};
// Tracks only user-command outcomes. Submitted values stay with their UI owner.
export function createReaderActions({ readerApi, onChanged }: {
    readerApi: ReaderApi;
    onChanged(): void;
}) {
    const commandIds=new Map<string,string>();
    const pending = new Map<string, {
        request: Request;
        problem: VisibleProblem;
    }>(), latest = new Map<string, number>();
    let sequence = 0, sending = false;
    function begin(path: string, method: string, body: unknown): Request | null {
        if (!(method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE') || /^(tabs\/|debug|problems\/)/.test(path))
            return null;
        const fields = body && typeof body === 'object' ? Object.keys(body) : [];
        const field = fields.length === 1 ? fields[0] : '';
        const request: Request = { id: crypto.randomUUID(), method, path: '/api/' + path, fields, result: 'unknown', sequence: ++sequence, key: '' };
        request.key = method + ':' + request.path + ':' + field;
        latest.set(request.key, request.sequence);
        return request;
    }
    function beginCommand(context:CommandContext):Request {
        let path='';let method:Request['method']='POST';let fields:string[]=[];
        if(context.operation==='settings_patch') {path='settings/'+context.target.scope+'/'+context.target.owner;method='PATCH';fields=context.keys;}
        else if(context.operation==='feed_patch') {path='feeds/'+context.feed_id;method='PATCH';fields=context.fields;}
        else if(context.operation==='codex_save'||context.operation==='processor_save') {path='processors/'+(context.operation==='codex_save'?'codex':'llamacpp');method='PUT';}
        else if(context.operation==='codex_check'||context.operation==='processor_check')path='processors/'+(context.operation==='codex_check'?'codex':'llamacpp')+'/check';
        else if('material_id' in context) {
            const suffix={viewed:'viewed',regenerate:'regenerate',retry_material:'retry',retry_theses:'theses/retry',retry_filter:'filter/retry',restore_filter:'filter/restore'}[context.operation];
            path='materials/'+context.material_id+'/'+suffix;
        }
        else if(context.operation==='complete_folder')path='folders/'+context.folder_id+'/completion';
        else if(context.operation==='collect')path='collect';
        else if(context.operation==='youtube_configure'){path='youtube';method='PATCH';}
        else if(context.operation==='add_feed')path='feeds';
        else if(context.operation==='history_start'||context.operation==='history_pause'||context.operation==='history_cancel'){path='feeds/'+context.feed_id+'/history';method=context.operation==='history_start'?'POST':context.operation==='history_pause'?'PATCH':'DELETE';}
        const request:Request={id:crypto.randomUUID(),context,method,path:'/api/'+path,fields,result:'unknown',sequence:++sequence,key:method+':/api/'+path+':'+(fields.length===1?fields[0]:'')};
        latest.set(request.key,request.sequence);
        commandIds.set(request.key,request.id);
        return request;
    }
    function describe(request: Request, unknown: boolean): VisibleProblem {
        const setting = request.path.match(/^\/api\/settings\/(oko|folder|feed)\/([\w-]+)$/);
        const material = request.path.match(/^\/api\/materials\/([\w-]+)\//);
        let action: ProblemAction | null = null, objects: ProblemObject[] = [], title = 'Не удалось выполнить действие';
        if (setting) {
            action = { kind: 'settings', page: request.fields.length === 1 ? 'field/' + request.fields[0] : 'home' };
            if (setting[1] !== 'oko')
                Object.assign(action, { scope: setting[1], id: setting[2] });
            objects = [{ kind: setting[1] === 'feed' ? 'feed' : 'settings', id: setting[2]!, label: setting[1] === 'feed' ? 'Настройки фида' : 'Настройки Око' }];
            title = 'Не удалось сохранить настройку';
        }
        else if (/\/processors\//.test(request.path)) {
            action = { kind: 'settings', page: request.path.includes('/codex') ? 'codex' : 'llamacpp' };
            title = 'Не удалось сохранить настройки обработчика';
        }
        else if (request.path === '/api/youtube') {
            action = { kind: 'settings', page: 'youtube' };
            title = 'Не удалось сохранить настройки YouTube';
        }
        else if (material) {
            action = { kind: 'material', id: material[1] };
            objects = [{ kind: 'material', id: material[1]!, label: 'Материал' }];
        }
        else if (/^\/api\/feeds\/[\w-]+$/.test(request.path))
            action = { kind: 'feed-settings', id: request.path.split('/').at(-1) };
        else if (request.path === '/api/collect')
            action = { kind: 'collect' };
        else if (/^\/api\/(feeds|telegram)/.test(request.path))
            action = { kind: 'add', page: request.path.includes('/telegram') ? 'telegram' : 'rss' };
        if (unknown && action && !['settings', 'feed-settings', 'material', 'add'].includes(action.kind))
            action = null;
        return { id: 'local-' + request.id, request_id: request.id, operation: request.key, code: unknown ? 'action-unknown' : 'action-failed', section: unknown ? 'wait' : 'action',
            title: unknown ? 'Результат действия пока неизвестен' : title, consequence: unknown ? 'Действие не повторяется автоматически.' : 'Вернитесь к действию, чтобы исправить проблему.',
            next_step: unknown ? 'Результат будет сверен после восстановления связи.' : 'Черновик остаётся у исходной формы.', objects, action,
            created_at: Date.now() / 1000, next_at: null, state: 'current', finished_at: null, outcome: null, saved: false };
    }
    async function flush() {
        if (sending || !pending.size)
            return;
        sending = true;
        try {
            for (const [key, entry] of pending) {
                const { id, method, path, fields, result } = entry.request;
                const context=entry.request.context;
                const reply=context?{result:(await observeCommand({client:readerApi.client,body:{id,context,result}})).data.outcome}:(await reportReaderAction({ client: readerApi.client, body: { id, method, path, fields, result } })).data;
                if(context)reply.result=(await commandReceipt({client:readerApi.client,path:{identity:id}})).data.outcome;
                if (pending.get(key) !== entry)
                    continue;
                if (reply.result === 'succeeded' || reply.result === 'abandoned')
                    pending.delete(key);
                else
                    entry.problem.saved = true;
            }
        }
        catch { /* Pending errors are visible and retry on the next status response. */ }
        finally {
            sending = false;
            onChanged();
        }
    }
    function matchesDraft(value:string|undefined,path:string,field:string):boolean {
        if(!value?.startsWith('{'))return false;
        try {
            const context:unknown=JSON.parse(value);
            if(!context||typeof context!=='object'||!('operation' in context))return false;
            if(context.operation==='codex_save')return path==='processors/codex';
            if(context.operation==='processor_save')return path==='processors/llamacpp';
            if(context.operation==='settings_patch'&&'target' in context&&'keys' in context){
                const target=context.target;
                return !!target&&typeof target==='object'&&'scope' in target&&'owner' in target&&path==='settings/'+target.scope+'/'+target.owner&&Array.isArray(context.keys)&&context.keys.length===1&&context.keys[0]===field;
            }
        }catch{/* A historical operation key is not JSON. */}
        return false;
    }
    return {
        begin, beginCommand, flush,
        forgetMaterials(ids: string[]) {
            const deleted = new Set(ids);
            for (const [key, entry] of pending)
                if (entry.problem.objects.some(obj => obj.kind === 'material' && deleted.has(obj.id)))
                    pending.delete(key);
        },
        failed(request: Request | null, value: unknown) {
            const error: Failure = value && typeof value === 'object' ? value : {};
            if (!request || latest.get(request.key) !== request.sequence)
                return;
            request.result = error.confirmed ? 'failed' : 'unknown';
            pending.set(request.key, { request, problem: describe(request, !error.confirmed) });
            onChanged();
            void flush();
        },
        succeeded(request: Request | null) { if (request && latest.get(request.key) === request.sequence) {
            pending.delete(request.key);
            onChanged();
        } },
        merge(snapshot: ProblemSnapshot) {
            const values = [...snapshot.current, ...snapshot.recent];
            for (const [key, entry] of pending)
                if (values.some(p => (p.request_id === entry.request.id || p.operation === key) && p.code !== 'action-unknown'))
                    pending.delete(key);
            const current = [...snapshot.current, ...[...pending.values()].map(p => p.problem)];
            return { ...snapshot, current, count: current.length };
        },
        async discard(path: string, field: string, snapshot: ProblemSnapshot, method = 'PATCH') {
            const key = method + ':/api/' + path + ':' + field;
            const local = pending.get(key);
            if (local) {
                await flush();
                const status = await readerApi.status();
                snapshot = status.problems || snapshot;
            }
            const failed = snapshot.current.find(p => (p.operation === key || p.request_id === commandIds.get(key) || matchesDraft(p.operation,path,field)) && p.code === 'action-failed');
            if (failed)
                await abandonProblem({ client: readerApi.client, path: { identity: failed.id } });
            pending.delete(key);
            onChanged();
        },
    };
}
export type ReaderActions = ReturnType<typeof createReaderActions>;
