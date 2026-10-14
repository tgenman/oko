# Архив UI-прототипов

14 октября 2026 года оставшиеся локальные throwaway-исходники сохранены отдельно от main. Рабочий интерфейс использует реализации выбранных вариантов; переключатели вариантов и синтетические действия в него не входят.

Ветка: `codex/prototype-archive-20261014`.
Коммит: `320dbd80a5373c64199df676c61246fddfe5cceb`.

## Исходники

- `frontend/feed-tree-ios-prototype.html`
- `frontend/folder-icons-preview.html`
- `frontend/header-ios-prototype.html`
- `frontend/status-panel-prototype.html`
- `frontend/reader-surfaces-ios-prototype.html`
- `frontend/reader-surfaces-ios-prototype.css`
- `frontend/reader-surfaces-ios-prototype.js`
- `.scratch/status-messages/preview.py`

Архивное дерево содержит необходимые стили читателя. Для восстановления конкретного файла используйте `git show codex/prototype-archive-20261014:<путь>`; для просмотра макетов извлеките дерево ветки в отдельный временный каталог и запустите статический сервер из него. Не возвращайте throwaway-маршруты в рабочий frontend ради просмотра.

Ранние primary-source ветки также сохранены: `codex/header-ios-prototype`, `codex/reader-surfaces-ios-prototype`, `codex/status-panel-prototype`. Новый архив фиксирует последние локальные версии; старые коммиты не переписаны.

Планы, решения и исторические скриншоты остаются в локальном трекере: [шапка](../../.scratch/header-ios-preview/spec.md), [папки и фиды](../../.scratch/feed-tree-ios-preview/spec.md), [окно добавления и чтение](../../.scratch/reader-surfaces-ios-preview/spec.md), [панель состояния](../../.scratch/status-messages/spec.md).
