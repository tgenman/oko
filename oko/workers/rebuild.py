"""Collect a replacement set without publishing partial results."""
import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from collections.abc import Callable

from oko.collection.adapters import (AdapterErrorKind,AdapterException,AdapterIssue,ExternalReference,
                                     MediaReference,PublicationAdapter,PublicationContent)
from oko.storage.media import MediaOutput


from oko.collection.rebuild_types import StagedFile,StagedPublication


def _check(value: PublicationContent) -> None:
    issue=next((issue for issue in value.issues if issue.kind!=AdapterErrorKind.notice),None)
    if issue is not None:raise AdapterException(issue)
    if value.needs_fetch:
        raise AdapterException(AdapterIssue('publication-incomplete','Публикация ожидает сбора',AdapterErrorKind.temporary,value.metadata.reference))


async def collect(adapter: PublicationAdapter, articles: PublicationAdapter, reference: ExternalReference,
                  staging: Path, admitted: Callable[[],bool], active: set[Path]) -> tuple[StagedPublication,...]:
    """Fetch the root, direct linked articles and their files; never crawl deeper."""
    staging.mkdir(parents=True,exist_ok=True)
    root=await adapter.fetch(reference)
    _check(root)
    values=[(root,adapter)]
    seen={(reference.key.platform,reference.key.external_id)}
    for child in root.linked_publications:
        identity=(child.key.platform,child.key.external_id)
        if identity in seen:continue
        seen.add(identity)
        value=await articles.fetch(child)
        _check(value)
        if value.metadata.reference.key!=child.key:
            raise AdapterException(AdapterIssue('identity_changed','Источник вернул другую публикацию',AdapterErrorKind.permanent,child))
        values.append((replace(value,linked_publications=()),articles))
    staged=[]
    for value,provider in values:
        files=[]
        for media in value.media:
            if not admitted():raise asyncio.CancelledError
            path=staging/str(uuid4())
            active.add(path)
            try:
                with MediaOutput(path,admitted) as output:
                    actual=await provider.download_media(value.metadata.reference,media,output)
                if actual.key!=media.key or actual.resource_identity!=media.resource_identity:
                    raise AdapterException(AdapterIssue('media_changed','Источник изменил описание файла',AdapterErrorKind.temporary,value.metadata.reference,media.key))
                files.append(StagedFile(actual,path))
            except AdapterException as error:
                path.unlink(missing_ok=True)
                active.discard(path)
                if error.issue.kind==AdapterErrorKind.notice and error.issue.code=='subtitles-unavailable':
                    files.append(StagedFile(media,None,error.issue))
                    continue
                raise
        staged.append(StagedPublication(value,tuple(files)))
    return tuple(staged)
