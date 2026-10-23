"""Selective, versioned reading over the same Runtime and Store owners."""
from collections.abc import Callable
from oko.collection.external_media import saved_external
from oko.collection.telegram.reading import saved_telegraph
import sqlite3
import json
from typing import Any

from fastapi import FastAPI, Depends
from pydantic import TypeAdapter
from oko.reader.generated import models as wire
from oko.reader import projection_v3 as project, snapshots as facts
from oko.reader.api_v3 import ApiError, invalid, no_query
from oko.reader.composition import snippet, telegram_title
from oko.reader.runtime import Runtime
from oko.storage.reader_versions import ReaderVersions
from oko.storage import material_read
from oko.storage.reading import reading_snapshot, stable_reader_time, reader_now


def _state(status: str | None, error: str | None = None) -> str:
    if error and status != 'running':
        return 'waiting'
    return {'done':'ready','pending':'queued','off':'disabled',None:'disabled'}.get(status, status or 'disabled')


class VersionedReading:
    def __init__(self, runtime: Runtime, status: Callable[[], wire.RuntimeStatus],
                 material: Callable[[str], wire.ReadableMaterial], metadata: Callable[[], dict[str, Any]]) -> None:
        self.runtime, self.status, self.material, self.metadata = runtime, status, material, metadata
        self.final: dict[str, Any] | None = None

    def compact(self, identity: str) -> dict[str, Any] | None:
        store = self.runtime.require_store()
        row = store.db.execute('SELECT * FROM items WHERE id=? AND viewed=0',(identity,)).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = dict(id=identity,feed_id=row['feed_id'],title='Материал загружается',
            published_at=project.timestamp(row['published']) if row['published_known'] else None,
            source_url=row['url'],snippet='',availability='pending',loading=False,changed=False,
            revision_id=None,preview_url=None,hidden=False,filter_state='disabled',theses_state='disabled')
        if not row['complete']:
            result['loading']=bool(store.db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND status IN ('pending','running')",(identity,)).fetchone())
            return result
        header,text=store.original(identity)
        filtered=store.filters.read(identity)
        description=header.get('description','')
        preview=description or text
        if filtered['status']=='done' and not filtered['restored']:
            for _ in range(1 if description else 2):
                for fragment in filtered['removed']:
                    preview=preview.replace(fragment,'')
        settings=store.subscriptions.effective('feed',row['feed_id'])
        title=header['title']
        if settings['feed']['kind']=='telegram' and settings['feed'].get('transport')!='rss':
            title=telegram_title(header,text)
        folder=store.path(identity).parent
        image=next(iter(sorted(folder.glob('image-*'))),None)
        attachment=next((a for a in header.get('attachments',[]) if a['kind']=='image'),None)
        jobs={r['kind']:r for r in store.db.execute("SELECT kind,status,error FROM jobs WHERE item=? AND kind IN ('filter','theses')",(identity,))}
        theses=jobs.get('theses')
        theses_state=_state(theses['status'] if theses else 'pending',theses['error'] if theses else None) if store.processing.theses.signature(identity) else 'disabled'
        result.update(title=title,snippet=snippet(preview),availability='available',hidden=bool(filtered['hidden']),
            filter_state=_state(filtered['status'],filtered.get('error')),theses_state=theses_state,
            changed=bool(store.versions.previous(identity)),revision_id=store.content_version(identity),
            loading=bool(store.db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND (status='running' OR (status='pending' AND next<=?))",(identity,reader_now(store.clock()))).fetchone()),
            preview_url=header.get('_media_urls',{}).get(image.name,f'/api/v3/materials/{identity}/images/{image.name}') if image else f"/api/v3/materials/{identity}/media/{attachment.get('media_id',attachment['name'])}" if attachment else None)
        result['loading'] = result['loading'] or row['platform'] is not None and store.media.loading(identity)
        external = saved_external(header,f'/api/v3/materials/{identity}')
        result['post_web_unavailable'] = external['post_web_unavailable']
        result['external_attachment_kinds'] = list(dict.fromkeys(a['kind'] for a in external['external_attachments']))
        return result

    def text(self, identity: str) -> dict[str, Any]:
        store=self.runtime.require_store()
        row=material_read.item(store.db,identity)
        if row is None or row['viewed']:
            raise ValueError('Material unavailable')
        feed=material_read.feed(store.db,row['feed_id'])
        header,text=store.original(identity) if row['complete'] else ({},'')
        source=dict(kind=feed['kind'],feed_id=row['feed_id'],source_url=row['url'])
        if feed['kind']=='youtube':source['captions']=header.get('captions') or 'not_requested'
        if feed['kind']=='telegram':
            binding=json.loads(feed['reader_binding']) if feed['reader_binding'] else None
            saved={k:v for k,v in feed.items() if k not in ('reader_binding','telegram_topic')}
            saved.update(original_urls=json.loads(saved['original_urls']),initial_history=None)
            access=project.telegram_access(facts.SubscriptionFeed.model_validate(saved),binding)
            source.update(access=access.model_dump(mode='json'),message_ids=[str(n) for n in json.loads(row['members'])])
        settings=store.subscriptions.effective('feed',row['feed_id'],details=False)
        job=material_read.jobs(store.db,identity).get('fetch')
        loading=bool(job and (job['status']=='running' or job['status']=='pending' and (not row['complete'] or job['next']<=reader_now(store.clock()))))
        loading=loading or row['platform'] is not None and store.media.loading(identity)
        title=header.get('title','Материал загружается')
        if feed['kind']=='telegram' and feed['transport']!='rss' and row['complete']:title=telegram_title(header,text)
        data=dict(id=identity,title=title,feed_id=row['feed_id'],published_at=project.timestamp(row['published']).root if row['published_known'] else None,
            source_url=row['url'],source_position=row['source_order'],source=source,originals=settings['effective']['originals']['value'])
        if not row['complete']:
            return dict(data,availability='pending',content=dict(availability='pending',loading=loading))
        full=header.get('full',True) and (not header.get('members') or set(header['members'])==set(json.loads(row['members'])))
        from oko.reader.reading_v3 import completeness
        external = saved_external(header,f'/api/v3/materials/{identity}')
        return dict(data,availability='available',content=dict(availability='available',linked_content=saved_telegraph(header,f'/api/v3/materials/{identity}'),post_web_unavailable=external['post_web_unavailable'],text=text,description=header.get('description',''),reply_preview=header.get('reply_preview'),
            completeness=completeness(full,header.get('reason',''),external['external_only'],external['post_web_unavailable']).model_dump(mode='json'),loading=loading,
            changed=bool(store.versions.previous(identity)),source_deleted=store.versions.deleted(identity),
            revision_id=store.content_version(identity),version=store.versions.number(identity)))

    def media(self, identity: str) -> dict[str, Any]:
        store=self.runtime.require_store()
        row=material_read.item(store.db,identity)
        if not row or not row['complete']:
            return dict(revision_id=None,images=[],attachments=[],previous_versions=[])
        path=store.path(identity)
        header,_=store.original(identity)
        external = saved_external(header,f'/api/v3/materials/{identity}')
        return dict(post_web_unavailable=external['post_web_unavailable'],external_attachments=external['external_attachments'],revision_id=store.content_version(identity),
            images=[] if store.publications.owns(identity) else [f'/api/v3/materials/{identity}/images/{p.name}' for p in sorted(path.parent.glob('image-*'))],
            attachments=[dict(url=f"/api/v3/materials/{identity}/media/{a.get('media_id',a['name'])}",kind=a['kind'],mime=a['mime']) for a in header.get('attachments',[])],
            previous_versions=[dict(number=v['number'],revision_id=v['version'],url=v['url']) for v in store.versions.previous(identity)])

    def navigation(self) -> dict[str, Any]:
        store=self.runtime.require_store()
        folders=store.subscriptions.folders()
        versions=store.read_versions.batch({('folder',f['id']) for f in folders})
        counts={row['folder']:row['visible'] for row in store.db.execute('SELECT * FROM reader_folder_counts')}
        feeds=[dict(id=r['id'],count=r['visible'] or 0,has_unviewed=bool(r['unviewed'])) for r in store.db.execute('SELECT f.id,c.visible,c.unviewed FROM feeds f LEFT JOIN reader_feed_counts c ON c.feed=f.id ORDER BY f.rowid')]
        return dict(folders=[dict(id=f['id'],title=f['title'],position=f['position'] or 0,count=counts.get(f['id'],0),version=str(versions['folder',f['id']]['version'])) for f in folders],feeds=feeds,count=sum(f['count'] for f in feeds))

    async def finish(self) -> None:
        store=self.runtime._store
        if store is None:return
        store.read_versions.advance(self.metadata())
        data=self.status().model_dump(mode='json')
        row=store.read_versions.version('status','runtime')
        assert row is not None
        data['problems']['updated_at']=project.timestamp(row['changed'])
        self.final=dict(state='current',storage_id=store.read_versions.identity,resources=[dict(kind='status',id='runtime',state='changed',version=str(row['version']),data=data)])

    async def read(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.runtime.stopped and self.final is not None:
            final=self.final
            if body['storage_id'] is not None and body['storage_id']!=final['storage_id']:
                return dict(state='reset',storage_id=final['storage_id'])
            stopped_resources=[]
            status=final['resources'][0]
            for request in body['resources']:
                kind=request['kind']
                identity='runtime' if kind=='status' else 'navigation' if kind=='navigation' else ReaderVersions.list_identity(request['options']) if kind=='list' else request.get('id','list')
                if kind=='status':
                    if request['known_version'] and int(request['known_version'])>int(status['version']):
                        return dict(state='reset',storage_id=final['storage_id'])
                    stopped_resources.append(dict(kind=kind,id=identity,state='unchanged',version=status['version']) if request['known_version']==status['version'] else status)
                else:stopped_resources.append(dict(kind=kind,id=identity,state='unavailable',version=None,reason='runtime_stopped'))
            return dict(state='current',storage_id=final['storage_id'],resources=stopped_resources)
        async with self.runtime._actions:
            store=self.runtime.require_store()
            owner=store.read_versions
            now,wall=store.clock(),store.wall_clock()
            if body['storage_id'] is not None and body['storage_id']!=owner.identity:
                return dict(state='reset',storage_id=owner.identity)
            requested: dict[tuple[str,str], dict[str,Any]]={}
            lists: dict[str, tuple[dict[str,Any], Any, list[str] | None]]={}
            row: sqlite3.Row | None = None
            for request in body['resources']:
                kind=request['kind']
                if kind=='list':
                    options=request['options']
                    identity=owner.list_identity(options)
                    target=options['target']
                    scope=target['scope']
                    key=target.get('feed_id',target.get('folder_id',''))
                    exists=scope=='all' or store.db.execute('SELECT 1 FROM '+('feeds' if scope=='feed' else 'folders')+' WHERE id=?',(key,)).fetchone()
                    if not exists:
                        lists[identity]=(request,None,None)
                    else:
                        row=owner.list_version(options)
                        if request['known_version'] and int(request['known_version'])>row['version']:
                            return dict(state='reset',storage_id=owner.identity)
                        ids=owner.ids(options) if request['known_version']!=str(row['version']) else None
                        lists[identity]=(request,row,ids)
                        for item in ids if ids is not None else request['known_cards']:
                            requested.setdefault(('card',item),dict(kind='card',id=item,known_version=request['known_cards'].get(item)))
                else:
                    identity='runtime' if kind=='status' else 'navigation' if kind=='navigation' else request['id']
                    if (kind,identity) in requested:
                        raise ApiError(422,invalid('body','resources'))
                    requested[kind,identity]=request
            versions=owner.batch(set(requested))
            if any(r.get('known_version') and ((kind,identity) not in versions or int(r['known_version'])>versions[kind,identity]['version']) for (kind,identity),r in requested.items()):
                return dict(state='reset',storage_id=owner.identity)
            resources: list[dict[str,Any]]=[]
            nav=None
            with stable_reader_time(owner.offset,now,wall):
                # Status may reconcile its durable problems; keep it outside the
                # synchronous read snapshot used by all other projections.
                status_request=requested.get(('status','runtime'))
                status_data=None
                if status_request and status_request['known_version']!=str(versions['status','runtime']['version']):
                    status_data=self.status().model_dump(mode='json')
                    row=owner.version('status','runtime')
                    assert row is not None
                    versions['status','runtime']=row
                    status_data['problems']['updated_at']=project.timestamp(row['changed'])
                with reading_snapshot():
                    nav_request=requested.get(('navigation','navigation'))
                    if nav_request and nav_request['known_version']!=str(versions['navigation','navigation']['version']):
                        nav=self.navigation()
                        for feed in nav['feeds']:
                            requested.setdefault(('feed',feed['id']),dict(kind='feed',id=feed['id'],known_version=None))
                        versions.update(owner.batch({key for key in requested if key not in versions}))
                    changed_items={identity for (kind,identity),request in requested.items()
                        if kind in ('material','filter','theses','media') and (kind,identity) in versions
                        and request['known_version']!=str(versions[kind,identity]['version'])
                        and versions[kind,identity]['item_exists'] is not None and not versions[kind,identity]['viewed']}
                    from contextlib import nullcontext
                    with material_read.material_inputs(store.db,changed_items) if changed_items else nullcontext():
                        for (kind,identity),request in requested.items():
                            row=versions.get((kind,identity))
                            resource: dict[str,Any]=dict(kind=kind,id=identity)
                            if row is None or kind in ('card','material','filter','theses','media') and (row['item_exists'] is None or row['viewed']) or kind=='feed' and row['feed_exists'] is None:
                                resources.append(dict(resource,state='unavailable',version=str(row['version']) if row else None,reason='viewed' if row and row['viewed'] else 'not_found'))
                                continue
                            if request['known_version']==str(row['version']):
                                resources.append(dict(resource,state='unchanged',version=str(row['version'])))
                                continue
                            try:
                                if kind=='status': data=status_data
                                elif kind=='navigation': data=nav
                                elif kind=='feed':
                                    feeds=store.subscriptions.feeds(identity)
                                    data=project.subscription(facts.SubscriptionFeed.model_validate(feeds[0]),store.subscriptions.telegram.binding(identity)).model_dump(mode='json') if feeds else None
                                elif kind=='card':data=self.compact(identity)
                                elif kind=='media':data=self.media(identity)
                                elif kind in ('filter','theses'):
                                    from oko.reader.reading_v3 import saved_filter, theses_parts
                                    complete=material_read.item(store.db,identity)
                                    revision=store.content_version(identity) if complete and complete['complete'] else None
                                    if kind=='filter':
                                        filtered=facts.Filter.model_validate(store.filters.read(identity))
                                        data=dict(revision_id=revision,filter=saved_filter(self.runtime,identity,filtered).model_dump(mode='json'))
                                    else:
                                        theses=store.processing.theses
                                        summary,origin,ai=theses.read(identity),theses.provenance(identity),theses.status(identity)
                                        projected=theses_parts(facts.Summary.model_validate(summary) if summary else None,
                                            TypeAdapter(facts.CodexProvenance | facts.LocalProvenance | facts.UnknownProvenance).validate_python(origin) if origin else None,
                                            facts.AI.model_validate(ai) if ai else None,self.runtime.processing_wait(identity,'theses'))
                                        data=dict(revision_id=revision,theses=projected.model_dump(mode='json'))
                                else:data=self.text(identity)
                                if data is None:
                                    resources.append(dict(resource,state='unavailable',version=str(row['version']),reason='not_found'))
                                else:
                                    resources.append(dict(resource,state='changed',version=str(row['version']),data=data))
                            except (OSError,ValueError,KeyError,ApiError):
                                resources.append(dict(resource,state='failed',message='Reading entity temporarily unavailable'))
            for identity,(request,row,ids) in lists.items():
                resource=dict(kind='list',id=identity)
                if row is None:resources.append(dict(resource,state='unavailable',version=None,reason='scope_removed'))
                elif request['known_version']==str(row['version']):resources.append(dict(resource,state='unchanged',version=str(row['version'])))
                else:resources.append(dict(resource,state='changed',version=str(row['version']),data=dict(ids=ids,options=request['options'])))
            return dict(state='current',storage_id=owner.identity,resources=resources)


def register(app: FastAPI, runtime: Runtime, status: Callable[[], wire.RuntimeStatus],
             material: Callable[[str], wire.ReadableMaterial], metadata: Callable[[], dict[str, Any]]) -> None:
    reading=VersionedReading(runtime,status,material,metadata)
    runtime.reader_finish=reading.finish
    runtime.reader_advance=lambda: runtime.require_store().read_versions.advance(metadata())

    @app.post('/api/v3/reading/check',response_model=wire.ReadingCheckResponse,dependencies=[Depends(no_query)])
    async def check(body: wire.ReadingCheckRequest) -> wire.ReadingCheckResponse:
        return wire.ReadingCheckResponse.model_validate(await reading.read(body.model_dump(mode='json')))

    @app.get('/api/v3/reading/materials/{item}',response_model=wire.ReadingCheckResponse,dependencies=[Depends(no_query)])
    async def opened(item: str) -> wire.ReadingCheckResponse:
        from oko.reader.reading_v3 import identity
        identity(item)
        return wire.ReadingCheckResponse.model_validate(await reading.read(dict(storage_id=None,resources=[dict(kind=kind,id=item,known_version=None) for kind in ('material','filter','theses','media')])))
