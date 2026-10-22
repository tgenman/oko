"""Closed synthetic stores are reused only as sources of independent file copies."""
from copy import deepcopy
from functools import cache
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory, mkdtemp
from threading import RLock
from typing import Callable, Hashable, TypeVar

T = TypeVar('T')


class LibraryTemplates:
    def __init__(self):
        self.directory = TemporaryDirectory(prefix='oko-test-libraries-')
        self.entries = {}
        self.lock = RLock()

    def copy(self, root: Path, key: Hashable, build: Callable[[Path], T]) -> T:
        if root.exists() and any(root.iterdir()):
            raise ValueError('Library destination must be empty')
        with self.lock:
            if key not in self.entries:
                source = Path(mkdtemp(dir=self.directory.name))
                try:
                    result = build(source)  # Builder closes every Store before returning.
                except BaseException:
                    shutil.rmtree(source)
                    raise
                self.entries[key] = source, deepcopy(result)
            source, result = self.entries[key]
            shutil.copytree(source, root, dirs_exist_ok=True)
            return deepcopy(result)

    def close(self):
        self.entries.clear()
        self.directory.cleanup()


@cache
def templates() -> LibraryTemplates:
    # TemporaryDirectory's finalizer cleans up at process exit. No disk cache
    # survives a run, code change or independent worker process.
    return LibraryTemplates()
