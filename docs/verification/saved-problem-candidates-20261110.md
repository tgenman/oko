# Проверка загрузки сохранённых проблем

Загрузка сохранённых проблем медиа и обработки использует общий проход; SQL-условия для каждой группы и правила выбора идентификатора сохранены.

- `./scripts/check types lint python --files tests/reader/test_processing_problem_lifecycle.py tests/reader/test_independent_media.py tests/reader/test_problem_batch_recovery.py --compact` с синтетической PostgreSQL: 86 passed, 0 skipped; types и lint прошли.
- `./scripts/check browser --files tests/browser/test_status_v3_http.ts tests/browser/test_external_telegram_media.ts --compact`: 4 passed, 0 skipped. Скриншот панели статуса просмотрен.
- Независимое статическое ревью проверило оба SQL-запроса, шесть вызовов, пустой список, группировку, фильтрацию и идентичность; расхождений не выявлено.
