"""Domain consequences returned to the collection worker, without Job identity."""
from dataclasses import dataclass,field
from typing import Any

@dataclass
class CollectionEffects:
    fetch: dict[str,bool] = field(default_factory=dict)
    refresh_links: set[str] = field(default_factory=set)
    media: set[str] = field(default_factory=set)
    revalidate: dict[str,bool] = field(default_factory=dict)
    delete_problem_ids_before: dict[str,list[str]] = field(default_factory=dict)
    delete_problems_checked: set[str] = field(default_factory=set)
    fetch_problems_before: dict[int,list[dict[str,Any]]] = field(default_factory=dict)
    fetch_problems_checked: set[int] = field(default_factory=set)
    media_problems_before: dict[int,list[dict[str,Any]]] = field(default_factory=dict)
    media_problems_checked: set[int] = field(default_factory=set)
    processing_problems_before: dict[int,list[dict[str,Any]]] = field(default_factory=dict)
    processing_problems_checked: set[int] = field(default_factory=set)
    source_groups_before: dict[int,tuple[str,set[str]]] = field(default_factory=dict)
    source_evidence_before: dict[str,set[tuple[str,str]]] = field(default_factory=dict)
