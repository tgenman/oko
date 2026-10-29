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
    Migration(9, 'collection-status', (Path(__file__).parent / 'postgres' / '009-collection-status.sql').read_text()),
    Migration(10, 'feed-details', (Path(__file__).parent / 'postgres' / '010-feed-details.sql').read_text()),
    Migration(11, 'source-deletion', (Path(__file__).parent / 'postgres' / '011-source-deletion.sql').read_text()),
    Migration(12, 'job-execution-settings', (Path(__file__).parent / 'postgres' / '012-job-execution-settings.sql').read_text()),
    Migration(13, 'processing-inputs', (Path(__file__).parent / 'postgres' / '013-processing-inputs.sql').read_text()),
    Migration(14, 'journal-links', (Path(__file__).parent / 'postgres' / '014-journal-links.sql').read_text()),
    Migration(15, 'receipt-cleanup', (Path(__file__).parent / 'postgres' / '015-receipt-cleanup.sql').read_text()),
    Migration(16, 'reader-time', (Path(__file__).parent / 'postgres' / '016-reader-time.sql').read_text()),
    Migration(17, 'telegram-sync', (Path(__file__).parent / 'postgres' / '017-telegram-sync.sql').read_text()),
    Migration(18, 'telegram-read-reasons', (Path(__file__).parent / 'postgres' / '018-telegram-read-reasons.sql').read_text()),
    Migration(19, 'telegram-saved-backfill', (Path(__file__).parent / 'postgres' / '019-telegram-saved-backfill.sql').read_text()),
    Migration(20, 'remove-reader-list-projections', (Path(__file__).parent / 'postgres' / '020-remove-reader-list-projections.sql').read_text()),
    Migration(21, 'reader-counts', (Path(__file__).parent / 'postgres' / '021-reader-counts.sql').read_text()),
)
