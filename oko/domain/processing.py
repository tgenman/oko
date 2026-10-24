"""Prepared domain input shared by services and workers; no attempt identity."""
from dataclasses import dataclass
from typing import Literal
from oko.processing.contracts import ProcessingRequest

Kind = Literal['filter','theses']

@dataclass(frozen=True)
class ProcessingInput:
    item: str
    kind: Kind
    signature: str
    revision: str
    request: ProcessingRequest
    public_ai: bool
    no_input: bool



@dataclass(frozen=True)
class ProcessingWait:
    reason: Literal['processor_not_configured', 'public_ai_not_allowed', 'processor_unavailable', 'retry_scheduled']
    retry_at: float | None


@dataclass(frozen=True)
class ProcessingPreparation:
    outcome: Literal['model','no_input','waiting','disabled']
    input: ProcessingInput | None = None
    reason: str = ''
