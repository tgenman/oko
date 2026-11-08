"""Validated saved initial-history facts shared by collection and reader."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr
from pydantic.experimental.missing_sentinel import MISSING

class HistoryError(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    item: StrictStr = Field(..., min_length=1, title='Item')
    message: StrictStr = Field(..., title='Message')

class History(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    available: StrictInt = Field(..., ge=0, le=9007199254740991, title='Available')
    cancelled: StrictBool | MISSING = Field(default=MISSING, title='Cancelled')
    count: StrictInt | None = Field(..., title='Count')
    count_text: StrictStr | None = Field(..., title='Count Text')
    discovery_error: StrictStr | None | MISSING = Field(default=MISSING, title='Discovery Error')
    discovery_retry_at: StrictFloat | None | MISSING = Field(default=MISSING, title='Discovery Retry At')
    errors: list[HistoryError] = Field(..., title='Errors')
    frozen: StrictBool = Field(..., title='Frozen')
    incomplete: StrictInt = Field(..., ge=0, le=9007199254740991, title='Incomplete')
    loaded: StrictInt = Field(..., ge=0, le=9007199254740991, title='Loaded')
    mode: Literal['count'] = Field(..., title='Mode')
    paused: StrictBool = Field(..., title='Paused')
    requests: StrictInt | MISSING = Field(default=MISSING, ge=0, le=9007199254740991, title='Requests')
    resume: StrictInt | MISSING = Field(default=MISSING, title='Resume')
    retrying: StrictInt = Field(..., ge=0, le=9007199254740991, title='Retrying')
    run: StrictStr | MISSING = Field(default=MISSING, title='Run')
    scan_complete: StrictBool = Field(..., title='Scan Complete')
    since: StrictFloat | None = Field(..., title='Since')
    stop_reason: StrictStr | None | MISSING = MISSING
    state: Literal['pending', 'loading', 'done', 'selecting', 'paused', 'waiting', 'cancelled'] = Field(..., title='State')
    unavailable: StrictInt = Field(..., ge=0, le=9007199254740991, title='Unavailable')
    viewed: StrictInt = Field(..., ge=0, le=9007199254740991, title='Viewed')
    waiting: StrictBool | MISSING = Field(default=MISSING, title='Waiting')
    warnings: list[StrictStr] | MISSING = Field(default=MISSING, title='Warnings')

class InitialHistoryStatus(History):
    feed_id: StrictStr = Field(..., title='Feed Id')


class DamagedHistoryStatus(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feed_id: StrictStr = Field(..., title='Feed Id')
    state: Literal['damaged'] = 'damaged'
