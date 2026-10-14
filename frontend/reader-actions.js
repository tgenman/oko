// Tracks only user-command outcomes. Submitted values stay with their UI owner.
export function createReaderActions({transport, onChanged}) {
  const pending = new Map(), latest = new Map();
  let sequence = 0, sending = false;
  function begin(path, method, body) {
    if (!['POST','PUT','PATCH','DELETE'].includes(method) || /^(tabs\/|debug|problems\/)/.test(path)) return null;
    const fields = body && typeof body === 'object' ? Object.keys(body) : [];
    const field = fields.length === 1 ? fields[0] : '';
    const request = {id:crypto.randomUUID(),method,path:'/api/'+path,fields,result:'unknown',sequence:++sequence};
    request.key = method + ':' + request.path + ':' + field;
    latest.set(request.key, request.sequence);
    return request;
  }
  function describe(request, unknown) {
    const setting = request.path.match(/^\/api\/settings\/(oko|folder|feed)\/([\w-]+)$/);
    const material = request.path.match(/^\/api\/materials\/([\w-]+)\//);
    let action = null, objects = [], title = 'Не удалось выполнить действие';
    if (setting) {
      action = {kind:'settings',page:request.fields.length === 1?'field/'+request.fields[0]:'home'};
      if (setting[1] !== 'oko') Object.assign(action,{scope:setting[1],id:setting[2]});
      objects = [{kind:setting[1]==='feed'?'feed':'settings',id:setting[2],label:setting[1]==='feed'?'Настройки фида':'Настройки Око'}];
      title = 'Не удалось сохранить настройку';
    } else if (/\/processors\//.test(request.path)) {
      action={kind:'settings',page:request.path.includes('/codex')?'codex':'llamacpp'};title='Не удалось сохранить настройки обработчика';
    } else if (request.path === '/api/youtube') {action={kind:'settings',page:'youtube'};title='Не удалось сохранить настройки YouTube';}
    else if (material) {action={kind:'material',id:material[1]};objects=[{kind:'material',id:material[1],label:'Материал'}];}
    else if (/^\/api\/feeds\/[\w-]+$/.test(request.path)) action={kind:'feed-settings',id:request.path.split('/').at(-1)};
    else if (request.path === '/api/collect') action={kind:'collect'};
    else if (/^\/api\/(feeds|telegram|import)/.test(request.path)) action={kind:'add',page:request.path.includes('/import')?'opml':request.path.includes('/telegram')?'telegram':'rss'};
    if (unknown && action && !['settings','feed-settings','material','add'].includes(action.kind)) action=null;
    return {id:'local-'+request.id,request_id:request.id,operation:request.key,code:unknown?'action-unknown':'action-failed',section:unknown?'wait':'action',
      title:unknown?'Результат действия пока неизвестен':title,consequence:unknown?'Действие не повторяется автоматически.':'Вернитесь к действию, чтобы исправить проблему.',
      next_step:unknown?'Результат будет сверен после восстановления связи.':'Черновик остаётся у исходной формы.',objects,action,
      created_at:Date.now()/1000,next_at:null,state:'current',finished_at:null,outcome:null,saved:false};
  }
  async function flush() {
    if (sending || !pending.size) return;
    sending = true;
    try {
      for (const [key, entry] of pending) {
        const {id,method,path,fields,result} = entry.request;
        const reply = await transport.json('/api/problems/actions','POST',{id,method,path,fields,result});
        if (pending.get(key) !== entry) continue;
        if (reply.result === 'succeeded' || reply.result === 'abandoned') pending.delete(key);
        else entry.problem.saved = true;
      }
    } catch { /* Pending errors are visible and retry on the next status response. */ }
    finally {sending = false; onChanged();}
  }
  return {
    begin, flush,
    forgetMaterials(ids) {
      const deleted = new Set(ids);
      for (const [key,entry] of pending) if (entry.problem.objects.some(obj=>obj.kind==='material'&&deleted.has(obj.id))) pending.delete(key);
    },
    failed(request, error) {
      if (!request || error.name === 'AbortError' || latest.get(request.key) !== request.sequence) return;
      request.result = error.confirmed ? 'failed' : 'unknown';
      pending.set(request.key,{request,problem:describe(request,!error.confirmed)}); onChanged(); void flush();
    },
    succeeded(request) {if (request && latest.get(request.key) === request.sequence) {pending.delete(request.key); onChanged();}},
    merge(snapshot) {
      const values = [...snapshot.current,...snapshot.recent];
      for (const [key, entry] of pending) if (values.some(p=>p.request_id === entry.request.id || p.operation === key)) pending.delete(key);
      const current = [...snapshot.current,...[...pending.values()].map(p=>p.problem)];
      return {...snapshot,current,count:current.length};
    },
    async discard(path, field, snapshot, method = 'PATCH') {
      const key = method + ':/api/' + path + ':' + field;
      const local = pending.get(key);
      if (local) {
        await flush();
        const status = await transport.json('/api/status');
        snapshot = status.problems || snapshot;
      }
      const failed = snapshot.current.find(p=>p.operation===key && p.code==='action-failed');
      if (failed) await transport.json('/api/problems/'+failed.id+'/abandon','POST');
      pending.delete(key); onChanged();
    },
  };
}
