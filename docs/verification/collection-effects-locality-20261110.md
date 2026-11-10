# Проверка последствий сбора

`title_only_changes` перенесён из доменных последствий в снимок хранилища: его используют при формировании уведомлений, но не после возвращения последствий сборщику. Множество по-прежнему накапливается на протяжении всей порции.

- `./scripts/check types lint`: успешно.
- `./scripts/check python --files tests/reader/test_worker_transactions.py tests/reader/test_title_cancelled_enable_review.py tests/reader/test_linked_publications.py tests/reader/test_publication_rebuild_http.py --compact`: 62 passed, 0 skipped; синтетическая PostgreSQL.
- Независимое статическое ревью проверило обращения к полю, копирование последствий и повторные публикации; расхождений не найдено.
