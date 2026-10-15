import type {ApiReply} from './fixture-types';
import type {FeedVerification} from '../../frontend/src/api/generated/types.gen';
export async function addSources(api:<P extends string>(path:P,method?:string,body?:unknown)=>Promise<ApiReply<P>>,rows:[string,string,string][]) {
 await api('tabs/setup/heartbeat','POST');
 for(const [url,title,folder] of rows){
  const checked:FeedVerification=await api('v3/feeds/verify','POST',{url});
  if(checked.state==='existing')continue;
  await api('v3/feeds','POST',{url:checked.candidate.url,verification:checked.verification,title,folder,initial_history:{mode:'date',date:new Date(Date.now()-86400000).toISOString().slice(0,10),timezone:'UTC'}});

 }
 await api('tabs/setup/close','POST');
 return api('subscriptions');
}
