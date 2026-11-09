# Проверка жизненного цикла резервирования YouTube

Объединены наступление срока, отмена, проверка и перенос резервирования проблем загрузки публикаций и субтитров. Проверки выполнялись с синтетическими источниками и отдельной PostgreSQL 18; рабочая библиотека не использовалась.

- `./scripts/check types lint`: успешно.
- `./scripts/check python --files tests/reader/test_subtitle_problem_lifecycle.py tests/reader/test_youtube_null_fetch_event_draft.py tests/reader/test_reservation_pointer_cleanup.py tests/reader/test_youtube_access.py tests/reader/test_youtube_publications.py tests/reader/test_youtube_admission_event_draft.py`: 21 passed, 0 skipped. Покрыты откат и завершение ожидания, переход срока, ETag, перезапуск, доступ к YouTube и чтение публикаций.
- Независимое ревью сопоставило прежние реализации активации, отмены, проверки и переноса для обоих видов работы с общей реализацией; подтверждённых расхождений не найдено. Ревью было статическим.
