"""Resolve persisted media keys only within the library's managed root."""
from pathlib import Path


def resolve_key(root: Path, key: str | None) -> Path | None:
    if not key:
        return None
    relative = Path(key)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'media':
        return None
    path = root / relative
    try:
        media_root = (root / 'media').resolve()
        media_root.relative_to(root.resolve())
        path.resolve().relative_to(media_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return path


def existing_file(root: Path, key: str | None) -> Path | None:
    path = resolve_key(root, key)
    return path if path is not None and path.is_file() else None
