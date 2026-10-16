# Проверка #6: состояние работы и восстановление

GitHub: https://github.com/tgenman/oko/issues/6
База ревью: `0fa766b68f81f621e1e8c2d95307a13535cfab81`. Синтетические источники, профили и обработчики; временная SQLite. Личные данные не использовались.

## Соответствие

- C07.01: RuntimeStatus, lifetime/runtime_state/work и причины ожидания типизированы; UI не сравнивает русские стадии. Store отдаёт предметную сводку заданий.
- C07.02: сохраняются id, current/retrying/finished, affected, времена, последствия и недавняя история.
- C07.03: закрытый RecoveryIntent валидирует цель и обязательные поля; неизвестный kind, reconnect и отсутствующие material IDs отклоняются.
- C07.04: новые server records не содержат page/home/field/menu; браузер выбирает формы и выполняет типизированные команды с повторным допуском.
- C07.05: producer mapping покрывает collection/history/filter/theses/delete/storage, профили и ошибки команд. Внешние классы исключений остаются диагностикой, не расширяют публичный enum. Локальные I/O имеют отдельные коды.
- C07.06: старые v1/v2 records сохраняют id, timestamps, reason, consequence и outcome; без надёжных фактов показываются как legacy без придуманного действия. Миграция — проекция при чтении, без разрушительного переписывания истории. Новые записи маркируют producer/context/error; старый служебный формат остаётся до #7.
- C07.07–09: OPML/topics отсутствуют в новых действиях; identity/duplicate/profile/in-use имеют предметные коды. source-reconnect не создаётся. Роль берётся только из подтверждённой привязки; конфликт не перепривязывает и не удаляет источник.
- C10.04–05/07: тексты и группы определены стабильными кодами, раскрытая завершённая карточка сохраняет место и фокус, count отражает текущие проблемы. Неизвестный исход не предлагает повтор записи.
- C11.06: configure processor/profile/setting/source и inspect material открывают локальный контекст; retry collection/history/theses передают точную цель в существующие v3 команды.

## Фактическая проверка

Первоначальные HTTP RED выявили UI-поля в сохранённых фактах, потерю исторического consequence и глобальную цель collect вместо фида; исправлены. Browser RED выявил потерю наблюдения удаления при отменённом GET и воскресающий удалённый фид; исправлены разделением записи/чтения и сохранением подтверждённых удалений. Review RED выявил mutation recovery при неизвестном исходе collect; исправлено. Некоторые промежуточные наборы прерывались на устаревшей сборке после генерации контракта; после сборки повторены. Старые fixture/assertions мигрированы на v3, skips не добавлялись.

- Расширенный backend набор: 73 passed; types/contracts PASS (`20261016T062549Z-e20221ea`).
- Последний связанный набор: **39 Python passed, 30 browser passed**, contracts/tooling-types/Python types/browser types PASS, 28.35 s (`20261016T065830Z-c2e86d0c`).
- `npm run api:check`, OpenAPI validate (40 routes), frontend build/typecheck и diff check PASS.
- Независимое Spec review: 27 HTTP/schema passed (2.62 s), 3 browser passed, types PASS (`20261016T070938Z-57f8d241`); замечаний нет.
- Standards review: замечаний нет после исправления unknown outcome; проверен итоговый связанный отчёт.
- Playwright desktop/mobile screenshots просмотрены: текущая проблема, переход к модели, завершённая раскрытая карточка. Доказательства: [evidence/task6](evidence/task6).

Полный `./scripts/check all tools` остаётся финальным gate после #7; результаты ограниченных повторов не означают прохождение полного набора. Два исторических browser skip не менялись.
