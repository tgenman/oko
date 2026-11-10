# Проверка упрощения приёма публикаций

Удалён промежуточный DiscoveryService; проверка внешних ключей остаётся в сборщике перед сохранением страницы. Использовались синтетические источники и отдельная PostgreSQL 18.

- `./scripts/check python --files tests/reader/test_worker_transactions.py tests/reader/test_initial_history.py tests/reader/test_collection_request_errors.py --compact`: 53 passed, 0 skipped. Первоначальный запуск имел те же результаты тестов, но завершился с `checkout_changed` из-за исправления имени переменной во время работы.
- `./scripts/check types`: успешно после исправления конфликта имени. Предыдущий запуск types завершился ошибкой; lint в нём прошёл.
- Независимое статическое ревью не выявило изменения порядка проверки, транзакции и допуска Store.
