"""Reader-facing summaries of ongoing work."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkSummary:
    kind: str
    status: str
    feed: str | None
    count: int
    error_code: str | None
    retry_at: float | None
