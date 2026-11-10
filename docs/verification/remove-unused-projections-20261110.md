# Проверка удаления неиспользуемых функций

Удалены невызываемые одиночные проекции, старый одиночный путь синхронизации неполных публикаций и его помощник, невызываемые операции резервирования и YouTube, а также пустой recovery-вызов. Действующие пакетные операции и восстановление сохранены.

- Поиск по исходникам, тестам и миграциям не обнаружил вызовов удалённых функций; независимое статическое ревью подтвердило результат. Возможные внешние потребители внутренних Python-функций не проверены.
- `./scripts/check types lint`: успешно.
- `./scripts/check python --files tests/reader/test_incomplete_event_transition_external.py tests/reader/test_incomplete_reservation_draft.py tests/reader/test_youtube_admission_event_draft.py tests/reader/test_media_problem_lifecycle.py tests/reader/test_processing_problem_lifecycle.py --compact`: 43 passed, 0 skipped, синтетическая PostgreSQL.
