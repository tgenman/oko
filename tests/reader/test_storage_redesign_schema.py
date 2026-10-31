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


def test_settings_keep_false_and_clear_only_the_selected_override(postgres_url):
    from oko.storage.settings_store import SettingsStore

    ddl = (Path(__file__).parents[1] / 'support' / 'storage_redesign.sql').read_text()
    with psycopg.connect(postgres_url) as db:
        db.execute('CREATE SCHEMA target_settings_fixture')
        db.execute('SET LOCAL search_path TO target_settings_fixture')
        db.execute(ddl)
        db.execute("INSERT INTO folders(id,title,position) VALUES ('folder','Folder',0)")
        db.execute("""INSERT INTO feeds(id,platform,external_id,url,title,folder_id,adapter)
                      VALUES ('feed','rss','source','https://example.test','Feed','folder','rss')""")
        settings = SettingsStore(db)
        settings.patch('oko', 'global', {'theses': True, 'prompt_filters': 'Keep this prompt'})
        settings.patch('folder', 'folder', {'theses': False})
        settings.patch('feed', 'feed', {'theses': True})
        assert settings.values('oko', 'global') == {'theses': True, 'prompt_filters': 'Keep this prompt'}
        assert settings.values('folder', 'folder') == {'theses': False}
        settings.patch('feed', 'feed', {'theses': None})
        assert settings.values('feed', 'feed') == {}
        assert settings.values('folder', 'folder') == {'theses': False}
        assert settings.values('oko', 'global')['prompt_filters'] == 'Keep this prompt'
        db.rollback()


def test_settings_writes_are_invisible_until_commit_and_rollback_restores_previous_value(postgres_url):
    from uuid import uuid4
    from oko.storage.settings_store import SettingsStore

    ddl = (Path(__file__).parents[1] / 'support' / 'storage_redesign.sql').read_text()
    name = 'target_settings_' + uuid4().hex
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
        try:
            with psycopg.connect(postgres_url) as writer, psycopg.connect(postgres_url) as reader:
                for db in (writer, reader):
                    db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(name)))
                writer.execute(ddl)
                writer.commit()
                writer_settings, reader_settings = SettingsStore(writer), SettingsStore(reader)
                writer_settings.patch('oko', 'global', {'theses': False})
                assert reader_settings.values('oko', 'global') == {}
                writer.commit()
                assert reader_settings.values('oko', 'global') == {'theses': False}
                writer_settings.patch('oko', 'global', {'theses': True})
                writer.rollback()
                assert reader_settings.values('oko', 'global') == {'theses': False}
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
