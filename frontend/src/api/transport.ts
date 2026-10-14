import {decodeJson} from './errors';
export {createReaderApi} from './reader';
export * as operations from './generated/sdk.gen';

// Generic HTTP rules, without a second list of reader operations or wire types.
export function createTransport({fetch:adapter,token,origin}:{fetch:typeof fetch;token:string|null;origin:string}) {
  function headers(path:string,json=false):Record<string,string> {
    const result:Record<string,string>=json?{'Content-Type':'application/json'}:{};
    if(new URL(path,origin).origin===origin)result.Authorization='Bearer '+token;
    return result;
  }
  async function request(path:string,options:RequestInit):Promise<Response> {
    const response=await adapter(path,options);
    if(!response.ok)await decodeJson(response);
    return response;
  }
  return {
    async json(path:string,method='GET',body?:unknown,signal?:AbortSignal,actionId?:string):Promise<unknown> {
      const response=await request(path,{method,signal,cache:'no-store',headers:{...headers(path,true),...(actionId?{'X-Oko-Action':actionId}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});
      return decodeJson(response);
    },
    media(path:string,signal?:AbortSignal,cache?:RequestCache):Promise<Response> {
      return adapter(path,{signal,...(cache===undefined?{}:{cache}),headers:headers(path)});
    },
    close(path:string):Promise<Response> {return request(path,{method:'POST',keepalive:true,headers:headers(path)});},
  };
}
