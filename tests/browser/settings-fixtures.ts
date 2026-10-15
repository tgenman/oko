import type {reader} from './support.ts';
async function objects(r: Awaited<ReturnType<typeof reader>>) {
  await r.page.evaluate(() => {
    const t = readerTransport;
    const feeds = ['one', 'two'].map(id => ({id, title: id, kind: 'rss', transport: 'rss', folder_id: 'f', original_urls: ['https://source.test/' + id], importance: 'normal', public_ai: false}));
    t.replies.set('/api/subscriptions', {body: {folders: [{id:'f',title:'Учёба'}], feeds}});
    for (const path of ['folder/f', 'feed/one', 'feed/two']) t.replies.set('/api/v3/settings/' + path, t.replies.get('/api/v3/settings/oko/global')!);
    t.replies.set('/api/v3/processors/llamacpp', {body:{endpoint:'http://127.0.0.1:8080',model:'local',local_execution:true}});
  });
}

const models = [
  {model:'small',display_name:'Small',default:true,reasoning_efforts:['low','high'],default_reasoning_effort:'low',fast_supported:true},
  {model:'large',display_name:'Large',default:false,reasoning_efforts:['high'],default_reasoning_effort:'high',fast_supported:false},
];

export {objects, models};
