"""Domain consequences returned to the collection worker, without Job identity."""
from dataclasses import dataclass,field

@dataclass
class CollectionEffects:
    processing_items: set[str] = field(default_factory=set)
    parent_notifications: dict[str,bool] = field(default_factory=dict)
    source_title_changes: dict[str,bool] = field(default_factory=dict)
    source_url_changes: set[str] = field(default_factory=set)
    refresh_status: set[str] = field(default_factory=set)
    sync_processing: bool = False
    title_only_changes: set[str] = field(default_factory=set)
    fetch: dict[str,bool] = field(default_factory=dict)
    refresh_links: set[str] = field(default_factory=set)
    media: set[str] = field(default_factory=set)
    revalidate: dict[str,bool] = field(default_factory=dict)
