"""Domain consequences returned to the collection worker, without Job identity."""
from dataclasses import dataclass,field

@dataclass
class CollectionEffects:
    fetch: dict[str,bool] = field(default_factory=dict)
    refresh_links: set[str] = field(default_factory=set)
    media: set[str] = field(default_factory=set)
    revalidate: dict[str,bool] = field(default_factory=dict)
