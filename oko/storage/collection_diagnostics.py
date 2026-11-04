"""Storage-only snapshots needed to reconcile work after publication writes."""
from dataclasses import dataclass, field, fields
from typing import Any

from oko.domain.collection import CollectionEffects


@dataclass
class CollectionDiagnostics(CollectionEffects):
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

    def domain_effects(self) -> CollectionEffects:
        # The Job owner retains this storage-only snapshot; the service receives
        # an independent object containing only publication/media consequences.
        return CollectionEffects(**{field.name:getattr(self,field.name) for field in fields(CollectionEffects)})
