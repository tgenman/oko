from __future__ import annotations
from tests.support.collection_acceptance import save
"""Synthetic provider for behavioural scenarios, using the current adapter contract."""
from dataclasses import dataclass,replace
from copy import deepcopy
from tests.support.synthetic_material import SyntheticMaterial
from datetime import datetime,timezone
import hashlib
import json
from typing import Any, BinaryIO
from pydantic import JsonValue
from oko.collection.contracts import DiscoveryResult
from tests.support.legacy_source import Material, Publication, FetchResult
from tests.support.storage import Store, legacy_publication
from oko.storage.job_types import Job
from oko.collection.adapters import ExternalKey,ExternalReference,Platform,PublicationContent,PublicationMetadata,MediaReference,AdapterException,AdapterIssue,AdapterErrorKind,DiscoveryPage,FeedMetadata
from oko.collection.contracts import SourceError


@dataclass(frozen=True)
class Image:
    name: str
    data: bytes


@dataclass(frozen=True)
class Attachment:
    name: str
    data: bytes
    kind: str
    mime: str


class StructuredSource:

    def __init__(self,source: Any) -> None:
        self.source=source
        self.publications: dict[str, Publication]={}
        self.contents: dict[ExternalKey, PublicationContent]={}
        self.payloads: dict[tuple[ExternalKey,str,str | None], bytes]={}

    @property
    def changes(self) -> Any:
        return self.source.changes

    def publication_adapter(self) -> Provider:
        return Provider(self)

    def reference(self,publication: Publication) -> ExternalReference:
        return publication.adapter_reference or ExternalReference(ExternalKey(Platform.other_web,publication.url),publication.url)

    def describe(self,publication: Publication,material: Material) -> Material:
        reference=self.reference(publication)
        text='\n\n'.join(b.get('text','') for b in (material.adapter_content.content.get('blocks',[]) if material.adapter_content else []) if b.get('type')=='text' and b.get('role')=='body')
        blocks: list[dict[str,JsonValue]]=[dict(type='text',role='body',text=text)]
        if material.adapter_content:
            blocks.extend(deepcopy(block) for block in material.adapter_content.content.get('blocks',[]) if block.get('type')=='quote')
        refs=[]
        attachments: list[Image | Attachment]=[*material.images,*material.attachments] if isinstance(material,SyntheticMaterial) else []
        for attachment in attachments:
            raw=attachment.data
            digest=hashlib.sha256(raw).hexdigest()
            kind=getattr(attachment,'kind','image')
            mime=getattr(attachment,'mime','image/png')
            resource=MediaReference(attachment.name,'https://fixture.invalid/'+digest,attachment.name,mime,len(raw),digest)
            refs.append(resource)
            self.payloads[(reference.key,resource.key,digest)]=raw
            blocks.append(dict(type='media',role=kind,ref={'$media':resource.key}))
        value=PublicationContent(PublicationMetadata(reference,material.title,datetime.fromtimestamp(publication.published,timezone.utc) if publication.published_known else None),
            dict(blocks=blocks,extra={}),tuple(refs),needs_fetch=not material.full)
        self.contents[reference.key]=value
        return replace(material,adapter_content=value)

    async def discover(self,feed: ExternalReference,cursor: str,limit: int) -> DiscoveryPage:
        previous=json.loads(cursor)['synthetic_cursor'] if cursor else None
        result: DiscoveryResult=await self.source.discover(previous)
        publications=[]
        for publication in result.publications:
            assert isinstance(publication,Publication)
            self.publications[publication.id]=publication
            self.publications[self.reference(publication).key.external_id]=publication
            described=self.describe(publication,publication.teaser).adapter_content if publication.teaser else None
            publications.append(described or PublicationContent(PublicationMetadata(self.reference(publication),'',datetime.fromtimestamp(publication.published,timezone.utc) if publication.published_known else None),needs_fetch=True))
        state=json.dumps(dict(synthetic_cursor=result.cursor,history_enroll=[p.metadata.reference.key.external_id for p in publications],scan_complete=result.continuation is None))
        if result.retry_error:
            raise result.retry_error
        return DiscoveryPage(tuple(publications),state,result.continuation is not None)

    async def fetch(self,publication: ExternalReference) -> PublicationContent:
        return await self.publication_adapter().fetch(publication)

    async def download_media(self,publication: ExternalReference,media: MediaReference,output: BinaryIO) -> MediaReference:
        return await self.publication_adapter().download_media(publication,media,output)

    async def resolve_feed(self,url: str) -> FeedMetadata:
        raise NotImplementedError

    async def cursor_before(self,feed: ExternalReference,cursor: str,offset: int) -> str:
        raise NotImplementedError


class Provider:
    def __init__(self,owner: StructuredSource) -> None:
        self.owner=owner

    async def fetch(self,publication: ExternalReference) -> PublicationContent:
        if self.owner.source is not None:
            original=self.owner.publications.get(publication.key.external_id) or Publication(
                publication.url.rstrip('/').rsplit('/',1)[-1],publication.url,0,adapter_reference=publication)
            result: FetchResult=await self.owner.source.fetch(original)
            described=self.owner.describe(original,result.material).adapter_content
            assert described is not None
            return described
        return self.owner.contents[publication.key]

    async def download_media(self,publication: ExternalReference,media: MediaReference,output: BinaryIO) -> MediaReference:
        content=self.owner.contents[publication.key]
        current=next((value for value in content.media if value.key==media.key),None)
        if current is None or current.resource_identity!=media.resource_identity:
            raise AdapterException(AdapterIssue('media_changed','Synthetic resource changed',AdapterErrorKind.media_changed,publication,media.key))
        output.write(self.owner.payloads[(publication.key,media.key,media.resource_identity)])
        return current


def publication_packet(publication: Publication,material: Material | None=None,*,needs_fetch: bool=False) -> PublicationContent:
    """An explicit metadata packet for closed synthetic-library builders."""
    owner=StructuredSource(None)
    material=material or publication.teaser
    described=owner.describe(publication,material) if material else None
    if described and needs_fetch:
        assert described.adapter_content is not None
        described=replace(described,adapter_content=replace(described.adapter_content,needs_fetch=True))
    if described:
        assert described.adapter_content is not None
        return described.adapter_content
    return PublicationContent(PublicationMetadata(owner.reference(publication),'',datetime.fromtimestamp(publication.published,timezone.utc) if publication.published_known else None),needs_fetch=True)


def save_material(store: Store,job: Job,material: Material) -> None:
    """Build current publication/files through the same acceptance operations."""
    import asyncio
    from oko.storage.media import MediaOutput
    owner=StructuredSource(None)
    publication=legacy_publication(store,job.item)
    assert publication is not None
    save(store,job,owner.describe(publication,material))
    while store.db.execute("SELECT 1 FROM jobs j JOIN media_files m ON m.id=j.item WHERE m.publication_id=%s AND j.status='pending'",(job.item,)).fetchone():
        media_job=store.jobs.next_job(store.clock(),feeds=[store.item_feed(job.item)],media_only=True)
        assert media_job is not None
        download=store.media.begin(media_job.item,str(media_job.id)+'-'+str(media_job.generation),store.jobs.manual(media_job))
        assert download is not None
        try:
            with MediaOutput(download.temporary,lambda:store.jobs.valid(media_job)) as output:
                actual=asyncio.run(owner.publication_adapter().download_media(download.publication,download.media,output))
            from oko.storage.transaction import transaction
            from oko.storage.job_repository import JobRepository
            staged=store.media.stage(media_job.item,download,actual)
            with transaction(store.db):
                assert store.media.accept(media_job.item,download,actual,staged)
                JobRepository(store).finish(media_job)
        finally:
            store.media.release(download)
