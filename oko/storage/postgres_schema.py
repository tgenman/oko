"""PostgreSQL schema baseline and subsequent releases."""
from pathlib import Path

from oko.storage.postgres_migrations import Migration

MIGRATIONS = (Migration(128, 'baseline', (Path(__file__).parent / 'postgres' / 'baseline.sql').read_text()),)
