"""Local publication files and cleanup, independent of Job persistence."""
from collections.abc import Callable
from pathlib import Path


class PublicationFiles:
    def __init__(self, root: Path, active_paths: set[Path], fault: Callable[[str],None]) -> None:
        self.root, self.active_paths, self.fault = root, active_paths, fault

    def workspace(self, identity: str) -> Path:
        return self.root/'staging'/identity
