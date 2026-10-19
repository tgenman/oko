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

Завершённые планы, решения и исторические скриншоты удалены из рабочего дерева; их последняя версия доступна в [истории проекта](https://github.com/tgenman/oko/tree/365e2b38f86d6e2bedccbb40b14c615b6f069455/.scratch).

## Меню папки — 2026-10-19

Пользователь выбрал вариант A: список действий как у фида. Delete… блокирует
удаление папки с фидами и показывает предупреждение; ID папки не зависит от названия.

Primary-source ветка: `codex/folder-menu-prototype-20261019`.
Коммит: `6906d5d767094a88e578226b2c3371152660ae82`.
Прототип: `frontend/folder-menu-prototype.html`,
`frontend/src/reading/folder-menu-prototype.{tsx,css,mjs}`.
Обоснование и проверка: `.scratch/folder-context-menu/` в архивном коммите.
Исходники прототипа удалены из рабочего frontend после выбора; для просмотра
восстановите архив отдельно от рабочего читателя.

## Создание папки из меню перемещения — 19 октября 2026

Пользователь выбрал A: New Folder… под списком папок открывает отдельный диалог;
Create & Move создаёт папку и переносит фид. Альтернативы B/C и прототип сохранены
в локальной ветке `codex/create-folder-prototype-20261019`, коммит
`39c98c2c523095f8ee7b4ad5b9c6c533cae92967`.
Исходники: `frontend/create-folder-prototype.html`,
`frontend/src/reading/create-folder-prototype.{tsx,css,mjs}`.
Контекст реализации: `.scratch/create-folder-menu/implementation.md`.

## Включение и выключение Око — 19 октября 2026

Выбрана одна кнопка Stop/Start на том же месте. При выключении чтение и локальные
действия доступны, сбор/ИИ/подключения заблокированы; состояние сохраняется.
Исходный прототип очищал чтение, это изменено после интервью с пользователем.

Primary-source ветка: `codex/power-prototype-20261019` (локальная).
Коммит: `c1faa9ee9695398834f822cf0091178766b4d798`.
Исходник: `frontend/power-state-prototype.html`; интервью и проверка прототипа:
`.scratch/power-state-prototype/`. Реализация: [#47](https://github.com/tgenman/oko/issues/47),
[архитектура](../architecture/work-power.md).

## Настройки фида — 19 октября 2026

Выбран вариант B: поля Title/Link/Folder и описание слева, Activity справа.
Уточнение пользователя: Link неизменяемый, только выделение/копирование; рядом
маленькая кнопка Copy link. Макет первоначально позволял редактировать Link.

Primary-source ветка: `codex/feed-settings-prototype-20261019` (локальная).
Коммит: `1b1516cf09621304bdc9e9cde06908f85539bb6f`.
Исходники: `frontend/feed-settings-prototype.html`,
`frontend/src/settings/feed-settings-prototype.{tsx,css,md}`.
Спецификация: `.scratch/feed-settings/spec.md`.
Архив сохраняет все три варианта; рабочий редактор использует только B.
