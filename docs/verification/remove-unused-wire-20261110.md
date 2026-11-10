# Проверка удаления преобразований wire-моделей

Удалены неиспользуемые функции `normalize` и `to_domain` и связанные только с ними импорты. Поиск по исходникам, тестам и генератору, а также независимое статическое ревью не нашли их вызовов.

- `./scripts/check contracts types lint`: успешно.
- `./scripts/check build`: успешно; Python- и TypeScript-библиотеки соответствуют контракту.
- `./scripts/check http --compact`: повтор с синтетической PostgreSQL прошёл, 5 passed. Первоначальный запуск без обязательного `OKO_TEST_POSTGRES_URL` завершился ошибкой, 1 passed и 4 failed.
