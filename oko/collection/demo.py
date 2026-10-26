"""Explicit synthetic source on the current publication contract."""
from datetime import datetime,timezone
from typing import BinaryIO
from oko.collection.adapters import ExternalKey,ExternalReference,Platform,PublicationAdapter,PublicationContent,PublicationMetadata,MediaReference
from oko.collection.contracts import Publication, DiscoveryResult, FetchResult
from oko.collection.rss_source import material


def reference(identity: str) -> ExternalReference:
    url='https://example.org/oko/'+identity
    return ExternalReference(ExternalKey(Platform.other_web,url),url)


class DemoAdapter:
    async def fetch(self,publication: ExternalReference) -> PublicationContent:
        identity=publication.url.rsplit('/',1)[-1]
        titles={'1':'Око: первый материал','2':'Чтение без отвлечений','3':'Локальное хранение'}
        text=('Это тестовая публикация. Открытие и копирование ссылки сохраняют её.\n\n'
              'Кнопка «Просмотрено» удаляет текст из Око. Это демонстрационный источник, '
              'прошедший настоящую очередь и сохранённый в PostgreSQL.')
        return PublicationContent(PublicationMetadata(publication,titles[identity],datetime.fromtimestamp(1800000000-int(identity),timezone.utc)),
            dict(blocks=[dict(type='text',role='body',text=text)],extra={}))

    async def download_media(self,publication: ExternalReference,media: MediaReference,output: BinaryIO) -> MediaReference:
        raise ValueError('The demo has no media')


class DemoSource:
    changes: str | None=None

    def publication_adapter(self) -> PublicationAdapter:
        return DemoAdapter()

    async def discover(self,cursor: str | None) -> DiscoveryResult:
        return DiscoveryResult(tuple(Publication(str(i),reference(str(i)).url,1800000000-i,adapter_reference=reference(str(i))) for i in range(1,4)) if cursor is None else (), '3')

    async def fetch(self,publication: Publication) -> FetchResult:
        assert publication.adapter_reference is not None
        return FetchResult(material(await self.publication_adapter().fetch(publication.adapter_reference)))
