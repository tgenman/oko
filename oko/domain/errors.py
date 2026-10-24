from typing import Any, Literal


class ActionError(Exception):
    def __init__(self, kind: Literal['conflict', 'invalid', 'missing', 'unavailable'], detail: str | dict[str, Any], *, expected: bool = False, code: str | None = None) -> None:
        self.kind, self.detail = kind, detail
        self.code = code
        self.diagnostic_skip = expected
        super().__init__(str(detail))
