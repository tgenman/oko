# Poetry, lock и окружение

Status: done
Blocked by: —
Assignee: codex

Спецификация: [spec.md](../spec.md).

## Приёмка

Чистая установка Poetry sync; один lock; pins и изменения транзитивных зависимостей проверены.

## Comments

2026-10-15. База реализации и ревью: `2755194342be1ed87788db0534156314897608c4`. Незавершённый перенос браузерных тестов на TypeScript остаётся в основном checkout и в эту задачу не входит.

Окружение на macOS установлено по lock (67 installs), повторный sync без изменений. `poetry check --lock --strict` проходит; отсутствующий/устаревший lock отклоняется. Сравнение: [dependency-comparison.json](../dependency-comparison.json). Пользователь отдельно подтвердил Python `>=3.11,<3.15`.
