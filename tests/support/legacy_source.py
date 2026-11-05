"""Legacy-shaped synthetic input used only by test scenario builders."""
from copy import deepcopy
from dataclasses import dataclass
from oko.collection.adapters import ExternalReference, PublicationContent
from oko.collection.contracts import SourceError, SourceManifest, SourceObservation


@dataclass(frozen=True)
class Publication:
    id: str
    url: str
    published: float
    teaser: 'Material | None' = None
    members: tuple[int, ...] = ()
    published_known: bool = True
    adapter_reference: 'ExternalReference | None' = None



@dataclass(frozen=True)
class Material:
    title: str
    full: bool = True
    reason: str = ''
    members: tuple[int, ...] = ()
    captions: str | None = None
    collection_error: 'SourceError | None' = None
    description: str = ''
    source_manifest: SourceManifest | None = None
    adapter_content: 'PublicationContent | None' = None



@dataclass(frozen=True)
class FetchResult:
    material: Material
    observation: SourceObservation = SourceObservation()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'material', deepcopy(self.material))
