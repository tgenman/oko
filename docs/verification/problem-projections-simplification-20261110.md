# Проверка проекций проблем заданий

Одиночные проекции проблем загрузки публикации, медиа и обработки используют те же правила, что пакетные. Для медиа сохранено включение субтитров. Проверки выполнялись с синтетической PostgreSQL.

- `./scripts/check types lint`: успешно.
- `./scripts/check python --files tests/reader/test_processing_problem_lifecycle.py tests/reader/test_media_problem_lifecycle.py tests/reader/test_fetch_implicit_lifecycle.py tests/reader/test_subtitle_problem_lifecycle.py tests/reader/test_youtube_null_fetch_event_draft.py --compact`: 38 passed, 0 skipped.
- Независимое статическое ревью сопоставило параметры и фильтры прежних одиночных и пакетных проекций; расхождений не обнаружено.
