import type {CompletionConflict} from './scenario-data';

// Build canonical packets from compact scenario facts at the synthetic boundary.
export function installReadingFixtures() {
  window.readingFixtureReply=(url,method,body,status)=>{
    const path=url.split('?')[0];
    if(!body||typeof body!=='object')return body;
    if(status===409&&path.includes('/completion')&&'detail' in body){const value=body.detail as CompletionConflict;return {code:'composition_changed',message:value.message,command_id:null,diagnostic_id:null,details:{preview:{cards_count:value.preview.cards,publications_count:value.preview.originals,token:value.preview.token}}};}
    if(status>=400)return body;
    if(path.endsWith('/completion/preview')&&'cards' in body&&'originals' in body&&'token' in body)return {cards_count:body.cards,publications_count:body.originals,token:body.token};
    if(method==='POST'&&('ok' in body)&&path.startsWith('/api/v3/'))return {...body,command_id:'fixture-command'};
    return body;
  };
}
