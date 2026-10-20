# Пагинация истории RSS/Atom и явные причины недобора

Status: claimed
Assignee: Codex
GitHub: https://github.com/tgenman/oko/issues/63
Blocked by: Нет

Полный объём и критерии приёмки: [согласованная спецификация](../agreed-spec.md).
Один связный результат: source → сохранение → HTTP → интерфейс читателя.
База ревью: `4c13bffe63f8bd6cfc585bb765bd01c3ae034391`.
Граница проверки: HTTP с временными SQLite/файлами и синтетическим RSS/Atom;
Playwright с просмотром desktop/mobile скриншотов. Применяется TDD.

## Comments

2026-10-20: начата реализация. Рабочий экземпляр не перезапускается.
