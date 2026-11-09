"""Prepared domain input shared by services and workers; no attempt identity."""
from dataclasses import dataclass
from typing import Literal
from oko.processing.contracts import ProcessingRequest
from oko.processing.config import ProcessorConfig

Kind = Literal['filter','theses']

@dataclass(frozen=True)
class ProcessingInput:
    item: str
    kind: Kind
    input_hash: str
    request: ProcessingRequest
    public_ai: bool
    no_input: bool
    processor_config: ProcessorConfig | None = None
    public_processor: bool = True





@dataclass(frozen=True)
class ProcessingPreparation:
    outcome: Literal['model','no_input','waiting','disabled']
    input: ProcessingInput | None = None
    reason: str = ''
