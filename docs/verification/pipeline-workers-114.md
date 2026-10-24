# Подготовка трёх воркеров — #114

База: `2c8c9a0e19c7e4870411f31b6d02d07f14511a1e` (актуальный main при начале).
Изолированный managed worktree: pipeline-workers-114, ветка codex/114-pipeline-workers.
Основной грязный checkout, рабочая установка, подписки, Telegram и реальные модели
не использовались. Перенос запускался только на синтетических SQLite/файлах.

## Выполненные проверки до общего прогона

- HTTP/атомарность/фон: 13 passed.
- Совместимость #89: связанные/общие публикации, YouTube и Telegram client: 50 passed.
- Обработка/no_input/медиа после исправления стадий и регенерации: 44 passed.
- Job payload, история, Telegram-привязки, медиа, миграция: 73 passed.
- После сохранения source-wait и параметров прерванного выполнения,
  восстановления контекста и зависимости слоёв: последний целевой набор 30 passed.
- Строгая типизация пройдена; итоговый frozen snapshot и полный результат приведены ниже.

Промежуточные падения не считаются успешной проверкой. Исправлены: порядок
проекции стадии относительно finish; перевод ручной регенерации в ожидающую
стадию; сохранение source_kind при web configure; отказ вычислять ревизию для
удалённого/лишённого корня материала; параметры прерванного Codex-прохода и
обязательный source-wait. В тесте независимости источник сначала не достигался
из-за неверного синтетического курсора; исправлена фикстура, проверка прошла.

## Независимое ревью

Первый проход подтвердил потерю source_kind в web configure; исправление
закреплено test_web_configuration_keeps_proven_channel_type. Дополнительно
проверен и покрыт случай новой ревизии при неизменной подписи фильтра:
payload различает входы, старое поколение не повреждает замену.
Результаты финального повторного ревью и общего прогона приведены ниже.

## Граница поставки

Целевой код использует три независимых воркера вместо Runtime из #89.
Офлайн-инструмент готовит отдельный каталог и отчёт; существующий destination,
занятый источник, группы и неизвестные типы дают отказ. Проверяется текущий формат
Publication из #112/#113; формат до Publication явно требует прежнего конвертера.
Рабочее переключение всех трёх воркеров относится к #115 и не выполнено.

## Первый общий прогон

Frozen tree `a3f2cf5bb731b77792d02dead347d50b8c11e2f7`, база совпала с указанной выше.
Отчёт `.scratch/check-runs/20261024T091813Z-e345040f/report.json`:
Python — 826 passed, 15 failed; tools — 204 passed, 1 failed;
Node — 113 passed; Playwright — 221 passed, 2 штатных skipped.
Contracts/build/syntax/lint/types/browser-types/HTTP/entrypoint прошли.
Общий exit 1: серверные регрессии и три EOF whitespace; это не зелёный all.

Исправления после него: короткая транзакция отмены после adapter check и при
закрытом допуске; defer captions и ранние finish не оставляют unowned SQL;
ранняя инициализация источника сохраняет прежнюю готовность API до heartbeat;
Codex public-AI запрет проверяется до adapter.check; устаревшая ошибка старого
входа очищается в команде; ручной повтор ready-медиа сохраняет manual intent.
Тесты отсутствующих субтитров теперь ждут завершённый no_input с пустыми points,
согласно #89, вместо промежуточного None до завершения этапа. Модель не вызывается.

Дополнительные находки повторного ревью закрыты реальными регрессиями:
долговечные поля Telegram-профиля сохраняются при штатном lifecycle reset;
реальный DROP COLUMN payload эмулирует базу #113 и не переоткрывает stopped Job;
старые успех/ошибка download не возвращают прежний locator;
медиа не инвалидирует running fetch, новая description условно отменяет старый fetch.
Независимый повтор трёх целевых файлов дал 37 passed. Последние добавления —
явные четыре исхода и невозвращаемые токены ревизий — повторно прочитаны reviewer,
новых замечаний нет; собственный целевой набор с ними дал 66 passed.

## Матрица 22 сценариев #114

Все проверки используют настоящие временные SQLite/файлы. Сетевые границы —
синтетические adapters и управляемые барьеры, а не реальные источники/модели.

| № | Сценарий | Проверки |
|---|---|---|
| 1 | Догрузка во время модели: старые успех/ошибка | test_new_input_rejects_old_success_and_old_error_without_harming_replacement; linked-publications HTTP |
| 2 | Перезапуск после догрузки до соседнего крона | test_restart_after_new_input_before_the_processing_cron_keeps_the_work; legacy payload upgrade |
| 3 | Два фида, restart между фильтром/тезисами | test_common_feed_context_survives_restart_without_second_feed_substitution |
| 4 | Промпт против будущей модели/reasoning/Fast | publication-processing и codex-execution HTTP |
| 5 | no_input против ошибки получения | test_preparation_distinguishes_all_four_domain_outcomes; publication-processing HTTP |
| 6 | Выключенный этап/filter_out | publication-processing HTTP; preparation outcomes |
| 7 | failed/unavailable/cancelled и source-wait | test_periodic_pass_keeps_stopped_work_and_source_wait_after_restart; legacy payload; new revision wait |
| 8 | Неуспешная ручная регенерация | test_failed_regeneration_retains_only_current_results_across_restart |
| 9 | Два воркера готовы писать | test_two_workers_ready_to_write_commit_sequentially_on_one_connection |
| 10 | Модель ждёт; другой воркер/HTTP используют SQL | управляемые late-model tests; media-lane independence; source failure leaves filter/summary working |
| 11 | Чтение не видит частичную запись | test_uncommitted_result_is_invisible_to_an_independent_reader; HTTP transaction tests |
| 12 | Отказ/отмена до SQL | disabled_admission_never_begins; task_cancelled_before_sql_entry |
| 13 | Исключение и следующий запрос | result_and_done_survive_or_rollback_together; следующий transaction |
| 14 | Вложенный вызов без второго BEGIN/commit | nested_service_uses_one_begin_and_a_caught_failure_cannot_commit |
| 15 | Сбой после результата до finish | processing_after_result |
| 16 | Сбой после finish до commit | processing_after_finish |
| 17 | Сбой после commit до ответа | processing_after_commit + reopen |
| 18 | finish=0 | conditional_finish_zero_rows_rolls_back_domain_result |
| 19 | Сервис отклонил вход/разрешения | input_changed_during_adapter_check; rule/permission-change HTTP |
| 20 | Устаревшая ошибка адаптера | old error/model, old fetch error, old media error |
| 21 | Файл перемещён, SQL rollback | test_process_death_at_media_acceptance_never_exposes_uncommitted_bytes; cleanup protection |
| 22 | Ошибка инвалидации Job в HTTP-команде | test_action_transactions: настройки/версии/Job откатываются вместе |

Дополнительно: overlapping ticks не дублируют захват; отказ collection не останавливает
два других крона; все SQL scopes проверяются на отсутствие await; границы импортов
проверяются отдельно. Старые пути Runtime/reviews удалены, общие тестовые helpers
используют тот же JobRepository/transaction без production compatibility facade.


## Итоговый полный прогон

Отчёт [final-full.json](pipeline-workers-114/final-full.json), frozen tree
`dc9edc459e701e92ae1828c4e85d391af18dd04b`, exit **0**, 163,08 с:

| Набор | Результат |
|---|---|
| Python и HTTP | 858 passed |
| Инструменты | 205 passed |
| Node | 113 passed |
| Playwright | 221 passed, 2 штатных skipped |
| Contracts, build, syntax, lint, types, browser-types, tooling-types, diff, entrypoint | PASS |

Python 3.11.15, Node 26.12.0; зависимости, версии линтеров и HTTP-контракт не менялись.
Проверка актуального индекса — [staged-check.json](pipeline-workers-114/staged-check.json), PASS.
Старая staged-проверка до повторного git add отражала прежние EOF и была FAIL;
после добавления исправлений индекс проверен заново, её успех не подразумевался.

После полного прогона добавлен явный тест пункта 2 матрицы (перезапуск после
нового входа до соседнего крона); весь его файл прошёл **31 passed**:
[additional-recovery.json](pipeline-workers-114/additional-recovery.json).
Последующие изменения затрагивают тест/документы/доказательства. Production-файлы
побайтово совпадают с успешным frozen tree:
[snapshot-equivalence.json](pipeline-workers-114/snapshot-equivalence.json).

Независимое повторное ревью закрыло найденные замечания; после явных исходов и
токенов A → B → A проведено ещё одно чтение этих изменений, новых находок нет.
Reviewer отдельно получил 37 passed на трёх целевых файлах до последних
дополнительных сценариев; его запуск не выдаётся за весь all.

Свежие Playwright-снимки просмотрены: полная страница client-канала с локальным
медиа, конфликт состава завершения папки, неизвестный исход настройки в панели
статуса. Перекрытий/обрезанных действий на этих снимках нет. Проблемы/конфликт на
снимках — предусмотренные синтетические сценарии, не результат проверки реальных
источников. Файлы:
[канал](pipeline-workers-114/telegram-client-publication.png),
[завершение](pipeline-workers-114/folder-completion-conflict.png),
[неизвестный исход](pipeline-workers-114/settings-v3-desktop.png).

Первый неуспешный all сохранён отдельно:
[initial-full.json](pipeline-workers-114/initial-full.json). Его результаты и
ограниченные повторы не подменяют итоговый зелёный прогон.

## Остаток до рабочего переключения

#114 подготовлен в изолированной ветке. Коммит, PR, merge, реальная миграция,
пробный перенос личной базы и перезапуск установки в рамках этой работы не выполнялись.
Общая приёмка и единовременное переключение кода/хранилища/трёх воркеров относятся
к #115. Подтверждение типа старых Telegram Web-объектов не угадывается: неизвестное
останавливает перенос. Результаты synthetic adapters не доказывают доступность
настоящих источников и моделей.
