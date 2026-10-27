"""Selective, versioned reading over the same ReaderApplication and Store owners."""
from collections.abc import Callable
from oko.collection.external_media import saved_external
from oko.collection.telegram.reading import saved_preview
import json
from typing import Any

from fastapi import FastAPI, Depends
from pydantic import TypeAdapter
from oko.reader.generated import models as wire
from oko.reader import projection_v3 as project, snapshots as facts
from oko.reader.api_v3 import ApiError, invalid, no_query
from oko.storage.composition import telegram_title
from oko.application.reader import ReaderApplication
from oko.storage.reader_versions import ReaderVersions
from oko.storage.reading import reading_snapshot, stable_reader_time, reader_now


class VersionedReading:
    def __init__(self, runtime: ReaderApplication, status: Callable[[], wire.RuntimeStatus],
                 material: Callable[[str], wire.ReadableMaterial], metadata: Callable[[], dict[str, Any]]) -> None:
        self.runtime, self.status, self.material, self.metadata = runtime, status, material, metadata
        self.final: dict[str, Any] | None = None

    def compact(self, identity: str) -> dict[str, Any] | None:
        card = self.runtime.queries.card(identity)
        if card is not None and card['published_at'] is not None:
            card['published_at'] = project.timestamp(card['published_at'])
        return card

    def text(self, identity: str) -> dict[str, Any]:
        store=self.runtime.queries
        row=store.item(identity)
        if row is None or row['viewed']:
            raise ValueError('Material unavailable')
        feed,binding=store.feed(row['feed_id'])
        header,text=store.original(identity) if row['complete'] else ({},'')
        source=dict(kind=feed['kind'],feed_id=row['feed_id'],source_url=row['url'])
        if feed['kind']=='youtube':source['captions']=header.get('captions') or 'not_requested'
        if feed['kind']=='telegram':
            access=project.telegram_access(facts.SubscriptionFeed.model_validate(feed),binding)
            source.update(access=access.model_dump(mode='json'),message_ids=[str(n) for n in json.loads(row['members'])])
        settings=store.effective('feed',row['feed_id'],details=False)
        job=store.jobs(identity).get('fetch')
        loading=bool(job and (job['status']=='running' or job['status']=='pending' and (not row['complete'] or job['next']<=reader_now(store.clock()))))
        loading=loading or row['platform'] is not None and store.media_loading(identity)
        title=header.get('title','Материал загружается')
        if feed['kind']=='telegram' and feed['transport']!='rss' and row['complete']:title=telegram_title(header,text)
        data=dict(id=identity,title=title,feed_id=row['feed_id'],published_at=project.timestamp(row['published']).root if row['published_known'] else None,
            source_url=row['url'],source_position=row['source_order'],source=source,originals=settings['effective']['originals']['value'])
        if not row['complete']:
            return dict(data,availability='pending',content=dict(availability='pending',loading=loading))
        full=header.get('full',True) and (not header.get('members') or set(header['members'])==set(json.loads(row['members'])))
        from oko.reader.reading_v3 import completeness
        external = saved_external(header,f'/api/v3/materials/{identity}')
        linked=saved_preview(header)
        if row['platform']=='telegram':
            linked=store.linked_preview(identity) or linked
        return dict(data,availability='available',content=dict(availability='available',linked_content=linked,post_web_unavailable=external['post_web_unavailable'],text=text,description=header.get('description',''),reply_preview=header.get('reply_preview'),
            completeness=completeness(full,header.get('reason',''),external['external_only'],external['post_web_unavailable']).model_dump(mode='json'),loading=loading,
            changed=bool(header.get('changed')),source_deleted=store.source_deleted(identity),
            revision_id=store.content_version(identity),version=1))

    def media(self, identity: str) -> dict[str, Any]:
        store=self.runtime.queries
        row=store.item(identity)
        if not row or not row['complete']:
            return dict(revision_id=None,images=[],attachments=[])
        header,_=store.original(identity)
        external = saved_external(header,f'/api/v3/materials/{identity}')
        return dict(post_web_unavailable=external['post_web_unavailable'],external_attachments=external['external_attachments'],revision_id=store.content_version(identity),
            images=[],
            attachments=[dict(url=header.get('_media_urls',{}).get(a['name'],f"/api/v3/materials/{identity}/media/{a.get('media_id',a['name'])}"),kind=a['kind'],mime=a['mime']) for a in header.get('attachments',[])])

    def navigation(self) -> dict[str, Any]:
        return self.runtime.queries.navigation_versions()

    async def finish(self) -> None:
        store=self.runtime.final_queries if self.runtime.has_storage else None
        if store is None:return
        store.versions.advance(self.metadata())
        data=self.status().model_dump(mode='json')
        row=store.versions.version('status','runtime')
        assert row is not None
        data['problems']['updated_at']=project.timestamp(row['changed'])
        self.final=dict(state='current',storage_id=store.versions.identity,resources=[dict(kind='status',id='runtime',state='changed',version=str(row['version']),data=data)])

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
            store=self.runtime.queries
            owner=store.versions
            now,wall=store.clock(),store.wall_clock()
            if body['storage_id'] is not None and body['storage_id']!=owner.identity:
                return dict(state='reset',storage_id=owner.identity)
            requested: dict[tuple[str,str], dict[str,Any]]={}
            lists: dict[str, tuple[dict[str,Any], Any, list[str] | None]]={}
            row: dict[str, Any] | None = None
            for request in body['resources']:
                kind=request['kind']
                if kind=='list':
                    options=request['options']
                    identity=owner.list_identity(options)
                    target=options['target']
                    scope=target['scope']
                    key=target.get('feed_id',target.get('folder_id',''))
                    exists=store.scope_exists(scope, key)
                    if not exists:
                        lists[identity]=(request,None,None)
                    else:
                        row=owner.list_version(options)
                        if request['known_version'] and int(request['known_version'])>row['version']:
                            return dict(state='reset',storage_id=owner.identity)
                        ids=owner.ids(options) if request['known_version']!=str(row['version']) else None
                        lists[identity]=(request,row,ids)
                        for item in ids[:request.get('card_limit')] if ids is not None else request['known_cards']:
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
                    feed_resources = store.feed_resources({identity for (kind, identity), request in requested.items()
                        if kind == 'feed' and (kind, identity) in versions and versions[kind, identity]['feed_exists'] is not None
                        and request['known_version'] != str(versions[kind, identity]['version'])})
                    changed_items={identity for (kind,identity),request in requested.items()
                        if kind in ('card','material','filter','theses','media') and (kind,identity) in versions
                        and request['known_version']!=str(versions[kind,identity]['version'])
                        and versions[kind,identity]['item_exists'] is not None and not versions[kind,identity]['viewed']}
                    from contextlib import nullcontext
                    with store.inputs(changed_items) if changed_items else nullcontext():
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
                                    feed, binding = feed_resources[identity]
                                    data=project.subscription(facts.SubscriptionFeed.model_validate(feed),binding).model_dump(mode='json')
                                elif kind=='card':data=self.compact(identity)
                                elif kind=='media':data=self.media(identity)
                                elif kind in ('filter','theses'):
                                    from oko.reader.reading_v3 import saved_filter, theses_parts
                                    complete=store.item(identity)
                                    revision=store.content_version(identity) if complete and complete['complete'] else None
                                    if kind=='filter':
                                        filtered=facts.Filter.model_validate(store.filter(identity))
                                        data=dict(revision_id=revision,filter=saved_filter(self.runtime,identity,filtered).model_dump(mode='json'))
                                    else:
                                        summary,origin,ai=store.theses_parts(identity)
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


def register(app: FastAPI, runtime: ReaderApplication, status: Callable[[], wire.RuntimeStatus],
             material: Callable[[str], wire.ReadableMaterial], metadata: Callable[[], dict[str, Any]]) -> None:
    reading=VersionedReading(runtime,status,material,metadata)
    runtime.reader_finish=reading.finish
    runtime.reader_advance=lambda: runtime.queries.versions.advance(metadata())

    @app.post('/api/v3/reading/check',response_model=wire.ReadingCheckResponse,dependencies=[Depends(no_query)])
    async def check(body: wire.ReadingCheckRequest) -> wire.ReadingCheckResponse:
        return wire.ReadingCheckResponse.model_validate(await reading.read(body.model_dump(mode='json')))

    @app.get('/api/v3/reading/materials/{item}',response_model=wire.ReadingCheckResponse,dependencies=[Depends(no_query)])
    async def opened(item: str) -> wire.ReadingCheckResponse:
        from oko.reader.reading_v3 import identity
        identity(item)
        return wire.ReadingCheckResponse.model_validate(await reading.read(dict(storage_id=None,resources=[dict(kind=kind,id=item,known_version=None) for kind in ('material','filter','theses','media')])))
