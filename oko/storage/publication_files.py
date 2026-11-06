"""Local publication files and cleanup, independent of Job persistence."""
from collections.abc import Callable
from pathlib import Path
import shutil


class PublicationFiles:
    def __init__(self, root: Path, active_paths: set[Path], fault: Callable[[str],None]) -> None:
        self.root, self.active_paths, self.fault = root, active_paths, fault

    def workspace(self, identity: str) -> Path:
        return self.root/'staging'/identity

    def delete(self, identity: str) -> bool:
        self.fault('before_delete')
        for path in (self.root/'content'/identity, self.root/'staging'/identity, self.root/'media'/identity):
            if any(path==active or path in active.parents for active in self.active_paths):
                return False
            if path.exists():
                shutil.rmtree(path)
        self.fault('after_delete_files')
        return True
