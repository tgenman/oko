"""Rows shared by native PostgreSQL cursors and existing repository projections."""
from collections.abc import Iterator, Sequence
from typing import Any, overload


class Record:
    """Column-name lookup and positional iteration, matching repository row usage."""
    def __init__(self, columns: tuple[str, ...], values: Sequence[Any]) -> None:
        self._columns = columns
        self._values = tuple(values)
        self._index: dict[str, int] = {}
        for index, name in enumerate(columns):
            self._index.setdefault(name, index)

    @overload
    def __getitem__(self, key: int | str) -> Any: ...

    @overload
    def __getitem__(self, key: slice) -> tuple[Any, ...]: ...

    def __getitem__(self, key: int | str | slice) -> Any:
        return self._values[self._index[key] if isinstance(key, str) else key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> tuple[str, ...]:
        return self._columns
