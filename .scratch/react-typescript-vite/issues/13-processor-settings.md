# 13: Настройки обработчиков и выбор модели

**What to build:** Пользователь настраивает llama.cpp/Codex, проверяет подключение и сохраняет явный выбор модели, усилия и Fast.

Blocked by: 08 — Меню, переходы и одно поле настроек.

Status: done

Parent: [Спецификация](../spec.md). Подготовка тикета не разрешает реализацию.

- [x] Обе формы и каталог моделей используют generated DTO/SDK и React; секреты не попадают в contract examples или диагностику.
- [x] Сохранены отдельные черновики и виды действий, корректные saved/effective values и отсутствие выдуманных defaults.
- [x] Закрытие/возврат и переключение страницы корректно завершают контекст проверки; прерванная загрузка каталога повторяется при возвращении.
- [x] Поздняя проверка не меняет другой экран, завершённая запись учитывается и не стирает более новый ввод.
- [x] HTTP/browser используют синтетические обработчики и каталоги; старые renderers настроек моделей удалены.

- [x] Для затронутого сценария сохранены существующие API/security, оформление и поведение; реальные HTTP/browser-проверки выполнены на временных данных и синтетических adapters. Фактический результат записан отдельно.

Implementation component and model regressions: [actual verification](../../../docs/verification/react-model-settings.md). Awaiting settings integration of persistent React pages and removal of old model renderers before closure.

Verification: [React settings](../../../docs/verification/react-settings.md).
