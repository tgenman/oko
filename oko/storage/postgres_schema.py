"""Current PostgreSQL schema baseline."""
from pathlib import Path

from oko.storage.postgres_migrations import Migration

MIGRATIONS = (Migration(131, 'baseline', (Path(__file__).parent / 'postgres' / 'baseline.sql').read_text()),)
