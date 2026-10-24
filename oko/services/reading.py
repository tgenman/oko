"""Reading membership and captured completion, independent of task execution."""

from oko.storage.access import repository_access
from collections.abc import Callable
import secrets
from typing import Any, Literal
from oko.domain.errors import ActionError
from oko.storage.composition import FolderContents
from oko.storage.store import Store

class ReadingService:
    def __init__(self, repository: Callable[[], Store]) -> None:
        self._storage = repository
        self._completions: dict[str, tuple[tuple[str, str], FolderContents | None]] = {}

    @repository_access
    def viewed(self, identity: str) -> set[str]:
        repository = self._storage()
        members = set(repository.reading.members(identity)) | {identity}
        if not repository.viewed(identity):
            raise ActionError('missing', 'Материал не найден')
        return members

    def _contents(self, owner: str, scope: Literal['folder', 'feed']) -> FolderContents:
        repository = self._storage()
        try:
            return repository.reading.feed_contents(owner) if scope == 'feed' else repository.folder_contents(owner)
        except LookupError as error:
            raise ActionError('missing', str(error)) from None

    @repository_access
    def preview(self, owner: str, scope: Literal['folder', 'feed'] = 'folder') -> dict[str, Any]:
        contents = self._contents(owner, scope)
        token = secrets.token_urlsafe(32)
        if len(self._completions) >= 128:
            del self._completions[next(iter(self._completions))]
        self._completions[token] = ((scope, owner), contents)
        return dict(token=token, cards=len(contents.cards), originals=len(contents.members))

    @repository_access
    def complete(self, owner: str, token: str, scope: Literal['folder', 'feed'] = 'folder') -> set[str]:
        saved = self._completions.get(token)
        if not saved or saved[0] != (scope, owner):
            raise ActionError('conflict', dict(message='Подтверждение устарело. Проверьте количество снова.', preview=self.preview(owner, scope)))
        contents = saved[1]
        if contents is None:
            return set()
        if self._contents(owner, scope) != contents:
            raise ActionError('conflict', dict(message='Состав фида изменился. Подтвердите новое количество.' if scope == 'feed' else 'Состав папки изменился. Подтвердите новое количество.', preview=self.preview(owner, scope)))
        self._storage().viewed_set([identity for identity, _ in contents.members])
        return {identity for identity, _ in contents.members} | set(contents.cards)

    @repository_access
    def accepted(self, owner: str, token: str, scope: Literal['folder', 'feed']) -> None:
        self._completions[token] = ((scope, owner), None)
