"""Immutable, native PostgreSQL schema releases shipped with the reader."""
from pathlib import Path

from oko.storage.postgres_migrations import Migration


MIGRATIONS = (
    Migration(1, 'library', (Path(__file__).parent / 'postgres' / '001-library.sql').read_text()),
    Migration(2, 'functions', (Path(__file__).parent / 'postgres' / '002-functions.sql').read_text()),
    Migration(3, 'projections', (Path(__file__).parent / 'postgres' / '003-projections.sql').read_text()),
    Migration(4, 'reading-indexes', (Path(__file__).parent / 'postgres' / '004-reading-indexes.sql').read_text()),
    Migration(5, 'remove-telegram-source-claims', (Path(__file__).parent / 'postgres' / '005-remove-telegram-source-claims.sql').read_text()),
    Migration(6, 'publication-memberships', (Path(__file__).parent / 'postgres' / '006-publication-memberships.sql').read_text()),
    Migration(7, 'storage-identity', (Path(__file__).parent / 'postgres' / '007-storage-identity.sql').read_text()),
    Migration(8, 'feed-avatars', (Path(__file__).parent / 'postgres' / '008-feed-avatars.sql').read_text()),
)
