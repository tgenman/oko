"""Target-schema checks on a disposable PostgreSQL schema, not a migration."""
from pathlib import Path

import psycopg
from psycopg import sql


def test_empty_target_schema_has_expected_tables_and_no_application_triggers(postgres_url):
    ddl = (Path(__file__).parents[1] / 'support' / 'storage_redesign.sql').read_text()
    with psycopg.connect(postgres_url) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier('target_design_fixture')))
        db.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier('target_design_fixture')))
        db.execute(ddl)
        tables = {row[0] for row in db.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")}
        assert tables == {
            'publications', 'jobs', 'feed_publications', 'publication_links', 'media_files',
            'folders', 'feeds', 'settings', 'telegram_profiles', 'telegram_bindings',
            'telegram_claim_owners', 'telegram_read_state', 'telegram_read_members',
            'initial_history', 'history_members', 'state', 'youtube_access',
            'reader_action_results', 'reader_problems', 'oko_schema_migrations',
        }
        assert db.execute("""SELECT count(*) FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND NOT t.tgisinternal""").fetchone()[0] == 0
        db.rollback()
