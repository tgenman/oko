"""Closed synthetic stores are reused only as sources of independent file copies."""
from copy import deepcopy
from functools import cache
from pathlib import Path
import shutil
import fcntl
import hashlib
import os
import pickle
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


class RunLibraryTemplates:
    """Runner-owned closed seeds; locks publish completed builds across workers."""
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = RLock()

    def copy(self, root: Path, key: Hashable, build: Callable[[Path], T]) -> T:
        if root.exists() and any(root.iterdir()):
            raise ValueError('Library destination must be empty')
        identity = hashlib.sha256(pickle.dumps(key)).hexdigest()
        source = self.directory / identity
        metadata = self.directory / (identity + '.result')
        with self.lock, (self.directory / (identity + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not metadata.exists():
                # A failed/killed builder cannot publish a partial source.
                shutil.rmtree(source, ignore_errors=True)
                source.mkdir()
                try:
                    result = build(source)
                    pending = metadata.with_suffix('.pending')
                    pending.write_bytes(pickle.dumps(result))
                    pending.replace(metadata)
                except BaseException:
                    shutil.rmtree(source)
                    raise
            result = pickle.loads(metadata.read_bytes())
        # Sources are immutable after publication; copies can run concurrently.
        shutil.copytree(source, root, dirs_exist_ok=True)
        return result


@cache
def templates() -> LibraryTemplates | RunLibraryTemplates:
    directory = os.environ.get('OKO_TEST_LIBRARY_DIR')
    if directory:
        return RunLibraryTemplates(Path(directory))
    # Direct invocations retain process-owned temporary seeds. The runner owns
    # its separate directory and removes it only after all child suites exit.
    return LibraryTemplates()
