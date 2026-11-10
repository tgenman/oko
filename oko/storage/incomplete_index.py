"""Notify diagnostic projections when publication facts change."""
from typing import Any


def save_class(db: Any, item: str, content: Any) -> None:
    mark=getattr(db,'incomplete_group_mark',None)
    if mark is not None:mark([item])
