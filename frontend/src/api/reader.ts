import {createClient} from './generated/client';
import {status, heartbeat, closeTab, collect, stop} from './generated/sdk.gen';
import type {Status} from './generated/types.gen';
import {ReaderError,decodeJson} from './errors';
import {isBinaryOperation} from './generated/media.gen';
export {ReaderError} from './errors';

export type ActionRecord = {id:string};
export type ActionTracker = {
  begin(path:string, method:string, body:unknown): ActionRecord | null;
  succeeded(record:ActionRecord | null): void;
  failed(record:ActionRecord | null, error:unknown): void;
};

export function createReaderApi({fetch: adapter, token, origin, onError}: {fetch: typeof fetch; token: string | null; origin: string; onError?:(error:unknown, source:string)=>void}) {
  let tracker: ActionTracker | undefined;
  const reported = new WeakSet<object>();
  const client = createClient({baseUrl: origin, cache:'no-store', throwOnError:true,
    fetch: async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init);
      const url = new URL(request.url);
      if (url.origin !== origin) throw new ReaderError('Посторонний адрес API');
      const headers = new Headers(request.headers);
      headers.set('Authorization','Bearer '+token);
      const body = request.body ? await request.text() : undefined;
      const record = !headers.has('X-Oko-Action') ? tracker?.begin(url.pathname.slice('/api/'.length), request.method, body === undefined ? undefined : JSON.parse(body)) ?? null : null;
      if (record) headers.set('X-Oko-Action', record.id);
      // The adapter keeps the same public fetch boundary as the previous
      // reader, including controlled delayed replies used by browser checks.
      try {
        const response = await adapter(url.pathname+url.search, {
        method:request.method, headers, signal:request.signal, cache:'no-store',
        keepalive:request.keepalive,
          ...(body === undefined ? {} : {body}),
        });
        if(!response.ok || !isBinaryOperation(url.pathname,request.method))await decodeJson(response.clone());
        tracker?.succeeded(record);
        return response;
      } catch(error) {
        tracker?.failed(record,error);
        if (onError && url.pathname !== '/api/debug') {
          if (error instanceof Error) {
            Object.assign(error,{materialIds:url.pathname.startsWith('/api/materials/') ? [url.pathname.split('/')[3]] : []});
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
    setActionTracker(value:ActionTracker) {tracker = value;},
    wasReported(error:unknown) {return typeof error === 'object' && error !== null && reported.has(error);},
    async status(signal?: AbortSignal): Promise<Status> {
      const reply = await status({client, signal});
      return reply.data;
    },
    async heartbeat(tab: string) {return (await heartbeat({client, path:{tab}})).data;},
    async close(tab: string) {return (await closeTab({client, path:{tab}, keepalive:true})).data;},
    async collect(actionId?: string) {return (await collect({client, headers:actionId ? {'X-Oko-Action':actionId} : {}})).data;},
    async stop(actionId?: string) {return (await stop({client, headers:actionId ? {'X-Oko-Action':actionId} : {}})).data;},
  };
}

export type ReaderApi = ReturnType<typeof createReaderApi>;
