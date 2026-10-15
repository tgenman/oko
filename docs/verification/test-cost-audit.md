# Аудит тестов и стоимости полного прогона — 2026-10-15

## Объём и решения

База: `365e2b3`, отдельный worktree `faster-test-scenarios/oko`.
Пользователь поручил убрать дубли, уменьшить дорогие сценарии, отказаться от мобильной
поддержки и удалить тесты Swagger. Это снятие требований только к mobile и Swagger UI,
а не разрешение отключать проверку сохранности данных или контрактов.

| Группа | Решение и проверяемый риск |
|---|---|
| lifecycle | Оставить реальное завершение процессов, смерть во время записи/удаления, независимые сессии, отмену. Быстрый mock не доказывает восстановление после смерти процесса. |
| collection | Оставить RSS/Telegram/YouTube, версии источников, proxy и worker failures. Это разные внешние границы; все данные и adapters синтетические. |
| processing / reviews | Оставить выбор моделей, отмену, публикацию результата, очистку orphan, восстановление, актуальность и запрет повторной обработки. |
| reader | Оставить HTTP-сценарии сохранения, удаления, состава чтения, истории, безопасности и синхронизации. Проверки одинаковых свойств DTO объединить. |
| browser | Оставить действия и DOM/focus/late-response сценарии. HTTP-тест не доказывает корректный фокус и сохранение браузерного черновика. Mobile-only ветки удалить, бизнес-сценарии перенести на desktop. |
| API | Оставить schema/lexical примеры и strict generation: они недороги и ловят coercion/некорректные варианты. Реальные legacy HTTP ответы сверять с используемой legacy schema. |
| development | Оставить защиту точного Git index, изоляцию checkout, запреты candidate approval, остановку потомков и отчёты об ошибках. Сократить fixture и повторные запуски генераторов/типизации. |
| packaging | Оставить один installed-wheel smoke вне checkout без Node. Это отличается от запуска исходников. |
| Swagger UI | Удалить два browser-сценария и отдельный HTTP-тест страницы по прямому запросу пользователя. Это осознанное снятие покрытия Swagger, не найденный дубль. |

## Сделанные сокращения

- Runner: маленький checkout вместо всего репозитория; настоящие CLI, subprocess cleanup,
  ошибки, отчёты и guards сохранены. Candidate-контекст продукта больше не меняет предмет этих тестов.
- Candidate: маленький API repository вместо полного проекта; сохранены isolation,
  tampering, semantic diff и запрет approval.
- Staged governance: одна генерация fixture и две настоящие drift-проверки; подмена baseline
  в индексе по-прежнему отвергается. Матрица matching review evidence проверяется напрямую
  на двух маленьких документах, без повторной генерации SDK.
- Hooks: `.cts` и `.mts` проверяются вместе в одном индексе, обе диагностики обязательны.
  Сохранены оба направления расхождения index/worktree.
- Strict generation: timestamp и strict primitives используют один сгенерированный пакет;
  positive/negative mypy fixtures проверяются одним запуском с обязательным отсутствием
  ошибок в positive и всеми пятью ожидаемыми ошибками в negative.
- Frontend builder: реальный Vite/watch и API check, но type graph маленького приложения
  вместо всех инструментов продукта. Типы инструментов отдельно проверяет полный runner.
- Browser history: 3 материала вместо 60; viewed ID и повторная выборка из 2 сохранены.
  Проверка 60 материалов, восстановления и отсутствия per-video list requests остаётся backend.
- Дубли внешних references сведены в один небольшой governance-сценарий: missing local,
  remote `$ref` и remote `$dynamicRef`.
- Дубли debug timestamps сведены в HTTP delivery/retry: нормализация UTC, idempotent retry,
  отказ числа/boolean/невалидной строки/времени без timezone.
- Удалены мобильные CSS-ветки, мобильные list/detail state и кнопка feeds-back, мобильные
  inert/focus trap панели; общий desktop Back редактора и Escape/opener focus сохранены.

## Что этот аудит не утверждает

Похожее название backend и browser тестов не означает дубль: они наблюдают разные границы.
Не доказано, что каждый оставшийся assertion минимален. Массовое удаление коротких schema
или security tests даёт мало времени и ухудшает сигнал. Дорогие process/recovery сценарии
остаются частью полного прогона; локальная итерация выбирает затронутые файлы.

## Фактическая проверка

54 целевых development/API сценария: PASS за 57.03 s до последних небольших правок
дедупликации контрактов. Примеры стоимости call на текущей базе:

| Сценарий | До, s | После, s |
|---|---:|---:|
| staged codegen index/worktree | 15.55 | 10.40 |
| candidate tampering/approval | 7.73 | 2.10 |
| builder сохраняет старое поколение | 9.97 | 8.67 |
| watch публикует и сохраняет старые assets | 7.45 | 5.78 |
| tooling cts/mts (суммарно) | 4.83 | 1.93 |
| DTO mypy positive/negative | 1.90 | 1.31 |

Первый полный исходный прогон был прерван после browser-набора: 117 passed,
3 failed, 2 skipped, 92.95 s. Ошибки: два жёстких Swagger count=49 и лишняя
client-session mutation в ожидании теста черновиков. Отдельный дозамер исходного Python:
421 passed, 12 failed, 227.64 s. Его candidate TMPDIR не был перенесён, поэтому часть
ошибок относится к условиям дозамера (выход fixtures/wheel за candidate boundary).
Эти времена не являются успешным полным baseline и не складываются в точный before.
Исторические 316.17 s относятся к базе75f6fe1, до v3, и не дают честного процента
ускорения для текущего дерева.

Последовательный полный прогон после сокращения сценариев: **347.22 s**. Browser:
117 passed / 2 ранее отключённых skipped, 84.23 s. Python: 215.89 s, единственное падение —
исходный сценарий YouTube с несогласованными искусственными wall/monotonic clocks.
После согласованного продвижения обоих часов тот же сценарий прошёл за **0.26 s call**
(0.68 s pytest). Полный последовательный запуск не объявляется успешным.

## Разделение групп по последующему запросу пользователя

Обычный Python-набор исключает явно перечисленные тесты инструментов, а не весь каталог
development. Installed runtime, synthetic environment и fixture-contract acceptance
остаются обычными. `all` выбирает tools по изменению scripts/hooks/CI/API/dependency/type
inputs относительно merge-base с origin/main, включая index/worktree/untracked. При
неизвестной базе tools включаются. `tools` позволяет явный запуск; CI использует
`tools --if-changed --base` с базой PR/push.

После общей подготовки browser, Python и выбранные tools выполняются параллельно;
каждая группа имеет свой process/report/log/artifacts. Успех требует успеха всех выбранных
групп. Guard checkout и завершение потомков сохранены. 36 проверок runner прошли за
20.99 s: настоящий barrier доказывает overlap, отдельно проверены failure/report,
SIGINT обоих workers с потомками в отдельных process groups, mutation guard, выбор
ветки/index/worktree/untracked/deletion, all+tools без повторов и порядок build→tests.
Дополнительная проверка переноса инструмента за пределы scripts прошла за 0.57 s.

Итоговый полный запуск `PYTEST_ADDOPTS='--durations=0 --durations-min=0.01'
./scripts/check all --candidate api/openapi.yaml`: **165.64 s (2 min 46 s)**.
Все выбранные исполняемые проверки PASS; совместимость API сохраняет PENDING_REVIEW.
Это не разрешение обновить baseline или выпустить candidate.

| Фаза/группа | Время, s | Результат |
|---|---:|---|
| API generate + deterministic drift | 22.12 | PASS |
| Остальная подготовка, HTTP, завершение | 18.44 | PASS |
| Python приложения | 125.08 | 328 passed |
| Инструментальные тесты | 111.20 | 106 passed |
| Browser | 89.27 | 117 passed, 2 ранее отключённых skipped |
| Полный wall-clock | **165.64** | Все выбранные проверки прошли |

Три тестовые группы перекрываются; их время не суммируется. В основной HTTP-фазе ещё
9 passed, они исключены из следующего Python-набора. В этом запуске tools включились
по реальным изменениям runner/workflow/governance/fixtures. Состав тестов не сокращён
ради параллельности. Отдельное время обычного прогона без tool-изменений не измерялось.

По сравнению с последовательным запуском347.22 s полный wall-clock меньше на181.58 s
(52.3%, около2.1 раза). Сравнение включает исправление clocks, новые тесты orchestration
и разброс cold codegen; исходный запуск имел одно падение, итоговый — ни одного.
Это измерение всего изменения, а не изолированный эксперимент только с scheduler.

Локальные сырые отчёты и логи:
`.scratch/check-runs/test-cost-audit-20261015/{before-sequential,after-parallel}/`.
После итогового запуска менялась только документация/архив доказательств; `git diff --check`
прошёл. Основной checkout main остался чистым. Изменения находятся только в отдельном
worktree. Независимый reviewer проверил UI/state изменения, выбор групп, cancellation,
дедупликацию и guards; замечания исправлены и покрыты тестами.


## Самые дорогие оставшиеся файлы приложения

| Файл | Сумма pytest durations, s |
|---|---:|
| `tests/api/test_installed_package.py` | 15.96 |
| `tests/lifecycle/test_process.py` | 14.10 |
| `tests/collection/test_telegram.py` | 6.74 |
| `tests/reader/test_telegram_read_sync.py` | 5.74 |
| `tests/processing/test_theses.py` | 5.04 |
| `tests/collection/test_youtube.py` | 4.82 |
| `tests/reader/test_youtube_history.py` | 4.77 |
| `tests/processing/test_codex.py` | 4.76 |
| `tests/development/test_candidate_installed_smoke.py` | 4.12 |
| `tests/reviews/test_filters.py` | 3.77 |
