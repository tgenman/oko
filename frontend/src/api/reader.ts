import {randomId} from '../browser-platform';
import {createClient} from './generated/client';
import {status as runtimeStatus, createClientSession, renewClientSession, closeClientSession, stop, collect, setPower} from './generated/sdk.gen';
import type {CommandContext,RuntimeStatus} from './generated/types.gen';
import {ReaderError,decodeJson} from './errors';
import {isBinaryOperation} from './generated/media.gen';
export {ReaderError} from './errors';

export type ActionRecord = {id:string};
export type ActionTracker = {
  beginCommand?(context:CommandContext): ActionRecord;
  succeeded(record:ActionRecord | null): void;
  failed(record:ActionRecord | null, error:unknown): void;
};

export function createReaderApi({fetch: adapter, token, origin, onError}: {fetch: typeof fetch; token: string | null; origin: string; onError?:(error:unknown, source:string)=>void}) {
  let tracker: ActionTracker | undefined;
  const reported = new WeakSet<object>();
  let versionCheck: Promise<RuntimeStatus> | undefined;
  async function compatible(): Promise<RuntimeStatus> {
    if (!versionCheck) versionCheck = runtimeStatus({client}).then(reply => {
      if (reply.data.api_version.split('.')[0] !== '3') throw new ReaderError('Unsupported Oko API version');
      return reply.data;
    }).catch(error => {versionCheck=undefined; throw error;});
    return versionCheck;
  }
  const client = createClient({baseUrl: origin, cache:'no-store', throwOnError:true,
    fetch: async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init);
      const url = new URL(request.url);
      if (url.origin !== origin) throw new ReaderError('Unexpected API address');
      if (!['GET','HEAD'].includes(request.method)) await compatible();
      const headers = new Headers(request.headers);
      if(token)headers.set('Authorization','Bearer '+token);
      const body = request.body ? await request.text() : undefined;
      // The adapter keeps the same public fetch boundary as the previous
      // reader, including controlled delayed replies used by browser checks.
      try {
        const response = await adapter(url.pathname+url.search, {
        method:request.method, headers, signal:request.signal, cache:'no-store',
        keepalive:request.keepalive,
          ...(body === undefined ? {} : {body}),
        });
        if(response.status!==304&&response.status!==204&&(!response.ok || !isBinaryOperation(url.pathname,request.method)))await decodeJson(response.clone());
        return response;
      } catch(error) {
        if (error instanceof ReaderError && error.code==='storage_changed' && url.pathname.startsWith('/api/v3/publication/')) throw error;
        if (onError && !['/api/v3/diagnostics','/api/v3/command-observations'].includes(url.pathname) && !url.pathname.startsWith('/api/v3/commands/')) {
          if (error instanceof Error) {
            Object.assign(error,{materialIds:url.pathname.startsWith('/api/v3/materials/') ? [url.pathname.split('/')[4]] : url.pathname.startsWith('/api/v3/reading/materials/') ? [url.pathname.split('/')[5]] : []});
            reported.add(error);
          }
          onError(error,request.method+' '+url.pathname);
        }
        throw error;
      }
    },
  });
  client.interceptors.request.use((request,options)=>{
    if(options.parseAs === 'auto' || options.parseAs === undefined)options.parseAs=isBinaryOperation(new URL(request.url).pathname,request.method)?'blob':'json';
    return request;
  });
  return {
    client,
    async command<T>(context:CommandContext, send:(headers:Record<string,string>)=>Promise<T>):Promise<T> {
      const record=tracker?.beginCommand?.(context) ?? {id:randomId()};
      try {const result=await send({'X-Oko-Action':record.id});tracker?.succeeded(record);return result;}
      catch(error) {tracker?.failed(record,error);throw error;}
    },
    setActionTracker(value:ActionTracker) {tracker = value;},
    wasReported(error:unknown) {return typeof error === 'object' && error !== null && reported.has(error);},
    async status(signal?: AbortSignal): Promise<RuntimeStatus> {
      const reply = await runtimeStatus({client, signal});
      return reply.data;
    },
    lifetime: compatible,
    async createSession() {return (await createClientSession({client})).data;},
    async renewSession(identity: string) {return (await renewClientSession({client, path:{identity}})).data;},
    async closeSession(identity: string) {return (await closeClientSession({client, path:{identity}, keepalive:true})).data;},
    async collect(target:import('./generated/types.gen').CollectionTarget={scope:'all'}) {return this.command({operation:'collect',target},async headers=>(await collect({client,headers,body:target})).data);},
    async setPower(enabled:boolean) {return this.command({operation:'power',enabled},async headers=>(await setPower({client,headers,body:{enabled}})).data);},
    async stop(actionId?: string) {return (await stop({client, headers:actionId ? {'X-Oko-Action':actionId} : {}})).data;},
  };
}

export type ReaderApi = ReturnType<typeof createReaderApi>;
