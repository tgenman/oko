from __future__ import annotations
from tests.support.collection_acceptance import save
"""Synthetic provider for behavioural scenarios, using the current adapter contract."""
from dataclasses import dataclass,replace
from datetime import datetime,timezone
import hashlib
import json
from typing import Any, BinaryIO
from pydantic import JsonValue
from oko.collection.contracts import Material, Publication, DiscoveryResult, FetchResult
from oko.storage.store import Store
from oko.storage.job_types import Job
from oko.collection.adapters import ExternalKey,ExternalReference,Platform,PublicationContent,PublicationMetadata,MediaReference,AdapterException,AdapterIssue,AdapterErrorKind
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


@dataclass(frozen=True)
class SyntheticMaterial(Material):
    images: tuple[Image,...]=()
    attachments: tuple[Attachment,...]=()


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
        return ExternalReference(ExternalKey(Platform.other_web,publication.url),publication.url)

    def describe(self,publication: Publication,material: Material) -> Material:
        reference=self.reference(publication)
        blocks: list[dict[str,JsonValue]]=[dict(type='text',role='body',text=material.text)]
        if material.reply_preview:
            blocks.append(dict(type='quote',**material.reply_preview.model_dump()))
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

    async def discover(self,cursor: str | None) -> DiscoveryResult:
        previous=json.loads(cursor)['synthetic_cursor'] if cursor else None
        result: DiscoveryResult=await self.source.discover(previous)
        publications=[]
        for publication in result.publications:
            self.publications[publication.id]=publication
            publications.append(replace(publication,adapter_reference=self.reference(publication),
                teaser=self.describe(publication,publication.teaser) if publication.teaser else None))
        state=json.dumps(dict(synthetic_cursor=result.cursor,history_enroll=[p.id for p in publications],scan_complete=result.continuation is None))
        return replace(result,publications=tuple(publications),cursor=state)

    async def fetch(self,publication: Publication) -> FetchResult:
        original=self.publications.get(publication.id,publication)
        result: FetchResult=await self.source.fetch(original)
        return replace(result,material=self.describe(original,result.material))


class Provider:
    def __init__(self,owner: StructuredSource) -> None:
        self.owner=owner

    async def fetch(self,publication: ExternalReference) -> PublicationContent:
        if publication.key not in self.owner.contents:
            original=Publication(publication.url.rstrip('/').rsplit('/',1)[-1],publication.url,0,adapter_reference=publication)
            result: FetchResult=await self.owner.source.fetch(original)
            self.owner.describe(original,result.material)
        return self.owner.contents[publication.key]

    async def download_media(self,publication: ExternalReference,media: MediaReference,output: BinaryIO) -> MediaReference:
        content=await self.fetch(publication)
        current=next((value for value in content.media if value.key==media.key),None)
        if current is None or current.resource_identity!=media.resource_identity:
            raise AdapterException(AdapterIssue('media_changed','Synthetic resource changed',AdapterErrorKind.media_changed,publication,media.key))
        output.write(self.owner.payloads[(publication.key,media.key,media.resource_identity)])
        return current


def publication_packet(publication: Publication,material: Material | None=None,*,needs_fetch: bool=False) -> Publication:
    """An explicit metadata packet for closed synthetic-library builders."""
    owner=StructuredSource(None)
    material=material or publication.teaser
    described=owner.describe(publication,material) if material else None
    if described and needs_fetch:
        assert described.adapter_content is not None
        described=replace(described,adapter_content=replace(described.adapter_content,needs_fetch=True))
    return replace(publication,adapter_reference=owner.reference(publication),teaser=described)


def save_material(store: Store,job: Job,material: Material) -> None:
    """Build current publication/files through the same acceptance operations."""
    import asyncio
    from oko.storage.media import MediaOutput
    owner=StructuredSource(None)
    publication=store.publication(job.item)
    assert publication is not None
    save(store,job,owner.describe(publication,material))
    while store.db.execute("SELECT 1 FROM jobs j JOIN media_files m ON m.id=j.item WHERE m.publication_id=? AND j.status='pending'",(job.item,)).fetchone():
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
