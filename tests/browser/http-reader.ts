import type {TestContext} from 'node:test';
import type {BrowserContext} from 'playwright';
import type {ApiReply} from './fixture-types.ts';
import {expect} from 'playwright/test';
import {randomBytes} from 'node:crypto';
import {newContext} from './browser-context.ts';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import * as servers from './server-process.ts';
import assert from 'node:assert/strict';

export async function productionReader(t?: Pick<TestContext, 'signal' | 'after'>, options: {persistent?:boolean;hostname?:'oko';prepare?:(data:string)=>Promise<void>;collection?:boolean;processingDelay?:number}={}) {
  const data=await fs.mkdtemp(path.join(os.tmpdir(),'oko-history-http-'));
  const token=randomBytes(24).toString('base64url');
  let context: BrowserContext | undefined, scenario: Awaited<ReturnType<typeof servers.start>> | undefined, cleanup: Promise<void> | undefined;
  // Independent startup runs in parallel; cleanup waits for both owners,
  // including resources created after an abort or the other startup failing.
  const started=Promise.resolve().then(()=>options.prepare?.(data)).then(()=>servers.start(data,token,options.persistent,options.hostname,{collection:options.collection,processingDelay:options.processingDelay})).then(value=>{scenario=value;return value;});
  const contextStarted=newContext().then(value=>{context=value;return value;});
  const close=()=>cleanup ??= (async()=>{
    t?.signal.removeEventListener('abort',abort);
    await Promise.allSettled([started,contextStarted]);
    try {await context?.close();} finally {
      try {if(scenario) await servers.stop(scenario.id);}
      finally {await fs.rm(data,{recursive:true,force:true});}
    }
  })();
  const abort=()=>{void close().catch(()=>{});};
  t?.signal.addEventListener('abort',abort,{once:true});
  t?.after(close);
  try {
    const [{origin},readyContext]=await Promise.all([started,contextStarted]);
    t?.signal.throwIfAborted();
    const headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',Host:new URL(origin).host};
    const apiOrigin=options.hostname ? origin.replace('://oko:', '://127.0.0.1:') : origin;
    async function api<P extends string>(url: P,method='GET',body?: unknown): Promise<ApiReply<P>> {
      const response=await fetch(apiOrigin+'/api/'+(url.startsWith('v3/')?url:'v3/'+url),{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
      assert.equal(response.status,method==='POST'&&url.replace(/^v3\//,'')==='client-sessions'?201:200,await response.clone().text()); return response.json();
    }
    const page=await readyContext.newPage();
    t?.signal.throwIfAborted();
    return {page,context:readyContext,api,origin,token,close,openReader:async()=>{await page.goto(origin+'/#token='+token);await expect.poll(async()=>{const state=await api('v3/status');return state.lifetime.kind==='explicit'||state.active_client_sessions>0;}).toBe(true);},restart:()=>servers.restart(scenario!.id)};
  } catch(error) {await close();throw error;}
};
