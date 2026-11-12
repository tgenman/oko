"""Synthetic source and content builders using the production adapter contract."""
from __future__ import annotations
import base64
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from typing import Any, BinaryIO, cast
from oko.collection.adapters import ExternalKey, ExternalReference, Platform, PublicationContent, PublicationMetadata, MediaReference, AdapterException, AdapterIssue, AdapterErrorKind, DiscoveryPage, FeedMetadata
from tests.support.storage import Store, stored_publication
from tests.support.synthetic_material import SyntheticMaterial
from tests.support.job_claim import claim_job
from tests.support.collection_acceptance import save
from oko.domain.job import Job


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


def publication(url: str, published: float, content: PublicationContent | None = None, *, adapter_reference: ExternalReference | None = None) -> PublicationContent:
    reference = adapter_reference or ExternalReference(ExternalKey(Platform.other_web, url), url)
    metadata = PublicationMetadata(reference, content.metadata.title if content else '', datetime.fromtimestamp(published, timezone.utc))
    return replace(content, metadata=metadata) if content else PublicationContent(metadata, needs_fetch=True)


def publication_packet(publication: PublicationContent, material: PublicationContent | None = None, *, needs_fetch: bool = False) -> PublicationContent:
    if material is None:
        return publication
    return replace(material, metadata=replace(publication.metadata, title=material.metadata.title), needs_fetch=needs_fetch or material.needs_fetch)


class StructuredSource:
    def __init__(self, source: Any) -> None:
        self.source = source
        self.contents: dict[ExternalKey, PublicationContent] = {}

    @property
    def changes(self) -> Any:
        return self.source.changes

    def describe(self, publication: PublicationContent, material: PublicationContent) -> PublicationContent:
        value = publication_packet(publication, material)
        self.contents[value.metadata.reference.key] = value
        return value

    async def discover(self, feed: ExternalReference, cursor: str, limit: int) -> DiscoveryPage:
        previous = json.loads(cursor)['synthetic_cursor'] if cursor else None
        result = await self.source.discover(previous)
        state = json.dumps(dict(synthetic_cursor=result.cursor, history_enroll=[p.metadata.reference.key.external_id for p in result.publications], scan_complete=result.continuation is None))
        if result.retry_error:
            raise result.retry_error
        self.contents.update((p.metadata.reference.key, p) for p in result.publications)
        return DiscoveryPage(result.publications, state, result.continuation is not None)

    async def fetch(self, reference: ExternalReference) -> PublicationContent:
        if self.source is None:
            return self.contents[reference.key]
        content = cast(PublicationContent, await self.source.fetch(reference))
        discovered = self.contents.get(reference.key)
        published_at = discovered.metadata.published_at if discovered else content.metadata.published_at
        value = replace(content, metadata=replace(content.metadata, reference=reference, published_at=published_at))
        self.contents[reference.key] = value
        return value

    async def download_media(self, publication: ExternalReference, media: MediaReference, output: BinaryIO) -> MediaReference:
        content = self.contents[publication.key]
        current = next((value for value in content.media if value.key == media.key), None)
        if current is None or current.resource_identity != media.resource_identity:
            raise AdapterException(AdapterIssue('media_changed', 'Synthetic resource changed', AdapterErrorKind.media_changed, publication, media.key))
        output.write(base64.b64decode(current.locator.split(',', 1)[1]))
        return current

    async def resolve_feed(self, url: str) -> FeedMetadata:
        raise NotImplementedError

    async def cursor_before(self, feed: ExternalReference, cursor: str, offset: int) -> str:
        raise NotImplementedError


def save_material(store: Store, job: Job, material: PublicationContent) -> None:
    """Build current publication/files through the same acceptance operations."""
    import asyncio
    from oko.storage.media import MediaOutput
    owner = StructuredSource(None)
    existing = stored_publication(store, job.item)
    assert existing is not None
    save(store, job, owner.describe(existing, material))
    while store.db.execute("SELECT 1 FROM jobs j JOIN media_files m ON m.id=j.item WHERE m.publication_id=%s AND j.status='pending'", (job.item,)).fetchone():
        media_job = claim_job(store, store.clock(), feeds=[store.item_feed(job.item)], media_only=True)
        assert media_job is not None
        download = store.media.begin(media_job.item, str(media_job.id)+'-'+str(media_job.generation), store.jobs.manual(media_job))
        assert download is not None
        try:
            with MediaOutput(download.temporary, lambda: store.jobs.valid(media_job)) as output:
                actual = asyncio.run(owner.download_media(download.publication, download.media, output))
            from oko.storage.transaction import transaction
            from oko.storage.job_repository import JobRepository
            staged = store.media.stage(media_job.item, download, actual)
            with transaction(store.db):
                assert store.media.accept(media_job.item, download, actual, staged)
                JobRepository(store).finish(media_job)
        finally:
            store.media.release(download)
