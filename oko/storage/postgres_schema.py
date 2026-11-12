"""PostgreSQL schema baseline and subsequent releases."""
from pathlib import Path

from oko.storage.postgres_migrations import Migration

_POSTGRES = Path(__file__).parent / 'postgres'
MIGRATIONS = (
    Migration(128, 'baseline', (_POSTGRES / 'baseline.sql').read_text()),
    Migration(129, 'drop_legacy_problem_indexes', (_POSTGRES / '129_drop_legacy_problem_indexes.sql').read_text()),
)
