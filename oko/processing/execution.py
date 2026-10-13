"""Explicit Codex choices for future work; not a result freshness policy."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


UNKNOWN_REVIEW = 'Модель начатого обзора неизвестна; пересоздайте обзор'


class CodexChoice(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    model: str = Field(min_length=1, max_length=200, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$')
    reasoning_effort: str = Field(min_length=1, max_length=30, pattern=r'^[a-z_]+$')
    fast: bool = False


class CodexSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    filter: CodexChoice
    theses: CodexChoice
    events: CodexChoice
    digest: CodexChoice

    def validate_catalog(self, models: list[dict[str, Any]]) -> None:
        from oko.processing.contracts import ProcessingError
        by_id = {row['model']: row for row in models}
        for kind, value in self.model_dump().items():
            model = by_id.get(value['model'])
            if model is None:
                raise ProcessingError(f'{kind}: выбранная модель отсутствует в каталоге Codex')
            if value['reasoning_effort'] not in model['reasoning_efforts']:
                raise ProcessingError(f'{kind}: модель не поддерживает выбранное усилие рассуждения')
            if value['fast'] and not model['fast_supported']:
                raise ProcessingError(f'{kind}: модель не поддерживает Fast')

    def choice(self, operation: str) -> CodexChoice:
        kind = 'digest' if operation.startswith('digest') else operation
        return CodexChoice.model_validate(self.model_dump()[kind])
