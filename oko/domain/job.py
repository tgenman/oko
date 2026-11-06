"""Persisted attempt identity; no repository dependencies."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    item: str
    generation: int
