"""Current PostgreSQL schema baseline."""
from pathlib import Path

from oko.storage.postgres_migrations import Migration

MIGRATIONS = (Migration(130, 'baseline', (Path(__file__).parent / 'postgres' / 'baseline.sql').read_text()),)
