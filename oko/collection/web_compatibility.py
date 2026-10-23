"""The existing fetch worker can collect a feedless article through WebAdapter."""
from oko.collection.adapters import AdapterException
from oko.collection.contracts import (FetchRequirements,FetchResources,FetchResult,Publication,SourceError,SourceObservation)
from oko.collection.rss_compatibility import material
from oko.collection.telegram.compatibility import source_error
from oko.collection.web_adapter import WebAdapter


class WebArticleSource:
    fetch_requirements=FetchRequirements()
    changes='Статья загружается отдельно от анонса или поста.'

    def __init__(self, adapter: WebAdapter) -> None:
        self.adapter=adapter

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        if publication.adapter_reference is None:
            raise SourceError('Отсутствует внешний ключ статьи',retryable=False)
        try:
            return FetchResult(material(await self.adapter.fetch(publication.adapter_reference)),SourceObservation())
        except AdapterException as error:
            raise source_error(error)
