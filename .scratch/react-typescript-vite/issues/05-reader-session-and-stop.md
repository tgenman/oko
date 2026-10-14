# 05: Сессия вкладки, сбор и остановка

**What to build:** Пользователь запускает сбор, останавливает Око и работает с несколькими вкладками с прежней семантикой через typed API.

Blocked by: 03 — Первый React-сценарий через сгенерированные библиотеки.

Status: done
Assignee: primary implementer

Parent: [Спецификация](../spec.md). Подготовка тикета не разрешает реализацию.

- [x] Операции сессии, сбора и остановки подключены к generated DTO/SDK; toolbar отражает реальные исходы и доступность команд.
- [x] Один владелец управляет heartbeat, polling, BroadcastChannel и закрытием/восстановлением вкладки; прежние интервалы, lease/grace и допуск результатов сохранены.
- [x] Повторный React setup/cleanup не удваивает циклы и не отправляет stop/close; keepalive, token и диагностические headers сохраняются.
- [x] Неизвестный сетевой исход не вызывает повтор команды; смена области, просмотр и остановка не принимают старые ответы.
- [x] HTTP и Playwright подтверждают stop, закрытие последней вкладки, повторное открытие и межвкладочное согласование; выделен стабильный interface обновления для следующих UI-срезов.

- [x] Для затронутого сценария сохранены существующие API/security, оформление и поведение; реальные HTTP/browser-проверки выполнены на временных данных и синтетических adapters. Фактический результат записан отдельно.

## Result

Typed heartbeat/close/collect/stop share one session resource owner. Setup/dispose is idempotent and does not issue server commands. Common SDK fetch tracks command outcomes and diagnostic headers. A single React renderer composes exclusive surfaces; remaining owner renderers are transitional. See actual checks in verification-implementation.md.
