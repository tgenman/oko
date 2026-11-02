"""Shared interface for Telegram web collection."""
from typing import Any
import httpx
from oko.collection.adapters import PublicationAdapter
from oko.collection.contracts import DiscoveryResult, FetchResult, Publication, SourceObservation, SourceIdentity
from oko.collection.telegram.web import WebTelegram


class TelegramSource:
    changes = 'Telegram: known unread messages and attachments are checked again during collection. Missing messages are kept locally; their absence may mean deletion or lost access.'
    def __init__(self, client: WebTelegram, account: str, peer: int, initial_days: int, username: str | None = None, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http_transport = client.transport
        self.adapter = 'web'
        self.changes = 'Telegram web: visible public history only; hidden messages and unknown username changes may go undetected.'
        self.identity_info: dict[str, Any] | None = None
        self.username = username
        self.client, self.account, self.peer, self.initial_days = client, account, peer, initial_days
        self.initial_history: dict[str, Any] | None = None
        self.more = False
        self.gap: str | None = None

    def publication_adapter(self) -> PublicationAdapter:
        raise NotImplementedError

    async def discover(self,cursor: str | None) -> DiscoveryResult:
        raise NotImplementedError

    async def fetch(self,publication: Publication) -> FetchResult:
        raise NotImplementedError

    def observation(self) -> SourceObservation:
        return SourceObservation(self.gap, self.changes, SourceIdentity.from_info(self.identity_info), (self.identity_info or {}).get('description'))
