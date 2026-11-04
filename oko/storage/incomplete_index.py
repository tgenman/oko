"""Notify diagnostic projections when publication facts change."""
from typing import Any


def save_class(db: Any, item: str, content: Any) -> None:
    mark=getattr(db,'incomplete_group_mark',None)
    if mark is not None:mark([item])


def classify_newly_eligible(db: Any, ids: list[str]) -> None:
    mark=getattr(db,'incomplete_group_mark',None)
    if mark is not None:mark(ids)


def recover(store: Any) -> None:
    # The connection-local view derives its facts from durable rows on demand.
    return None
