"""Load the database credential file without putting its value in argv or env."""
from pathlib import Path
import stat


def private_database_url(path: Path) -> str:
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError('Database credentials must be a private regular file (0600)')
    value = path.read_text(encoding='utf-8').strip()
    if not value:
        raise ValueError('Database credentials file is empty')
    return value
