"""Detached replacement data; no storage or scheduler ownership."""
from dataclasses import dataclass
from pathlib import Path
from oko.collection.adapters import AdapterIssue,MediaReference,PublicationContent


@dataclass(frozen=True)
class StagedFile:
    media: MediaReference
    path: Path | None
    issue: AdapterIssue | None = None


@dataclass(frozen=True)
class StagedPublication:
    value: PublicationContent
    files: tuple[StagedFile,...]


@dataclass(frozen=True)
class PreparedFile:
    key: str
    identity: str
    storage_key: str | None
    media: MediaReference
    size: int | None
    issue: AdapterIssue | None = None


@dataclass(frozen=True)
class PreparedReplacement:
    identity: str
    value: PublicationContent
    files: tuple[PreparedFile,...]
