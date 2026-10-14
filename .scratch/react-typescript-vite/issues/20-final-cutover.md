# 20: Единый React-интерфейс и приёмка миграции

**What to build:** Пользователь получает целиком перенесённое Око с одним React root, полным API-first и готовой проверенной поставкой.

Blocked by: 04 — Готовая поставка Око и встроенный Swagger; 07 — Диагностика и неизвестные исходы команд; 10 — Подключение RSS; 11 — Подключение и переподключение Telegram; 12 — Импорт OPML; 13 — Настройки обработчиков и выбор модели; 15 — Доступ к YouTube и догрузка истории; 16 — Дерево областей чтения; 17 — Список карточек, превью и прокрутка; 18 — Открытый материал, версии и медиа; 19 — Обзоры и завершение тематической папки.

Status: done
Assignee: primary implementer

Parent: [Спецификация](../spec.md). Подготовка тикета не разрешает реализацию.

- [x] Все перенесённые владельцы собраны в один root; удалены временные legacy-adapters, старые entry/renderers и допуски production JavaScript.
- [x] Inventory подтверждает использование generated DTO/SDK всеми операциями, включая редко используемые routes; ручных дубликатов wire-схем и непроверенных обходов нет.
- [x] Полная сборка, обе генерации, совместимость/drift, HTTP/security, browser/lifecycle, Python/CLI и установленный пакет проходят на подготовленном дереве.
- [x] Swagger, SDK, backend и UI имеют один контракт; повторная генерация и сборка не меняют checkout, Node/генераторы не требуются обычному запуску.
- [x] Просмотрены итоговые desktop/mobile снимки; независимое ревью проверяет поведение, владельцев и границы. Фактические результаты и ограничения сохранены отдельно.
- [x] Действующая документация обновлена по результату; исторические отчёты сохранены, публикация приложения и запуск личных источников не следуют автоматически из готовности тикетов.

- [x] Для затронутого сценария сохранены существующие API/security, оформление и поведение; реальные HTTP/browser-проверки выполнены на временных данных и синтетических adapters. Фактический результат записан отдельно.

## Result

SingleReactroot, strictTS entry, alllegacyrenderers/shims removed. All50canonicaloperations generateDTO/SDK; backendbody/output integration, binary handling, docs/wheel andgatechecks validated. Fullchecks plusaffectedrepeat andindependentre-review recorded in [verification](../verification-implementation.md); previousfailures preserved.
