> Исторический документ. С 2026-10-15 обзоры за период, события и сжатие истории удалены из Око. Их описание ниже сохраняет контекст и результаты прежней версии, не задаёт требований к текущей реализации. Сохранены фильтрация и тезисы отдельных публикаций.

# Стоимость локального чтения — 2026-10-15

Продолжение расследования после исправления голодания браузерного polling. Пользователь указал, что 1.36 s для открытия локального фида неприемлемы.

## Подтверждённая причина

Живой `/api/status` показал «Ожидание повтора», ни одного running job: 4 discover pending с ошибкой доступности YouTube; 49 fetch pending, из них 48 ждут допуска субтитров и 1 — снятия ограничения YouTube. Готовы 9 тезисов. Фоновая сеть не была установлена причиной задержки.

На временной копии SQLite и сохранённых материалов cProfile и SQLite trace выявили:

- ReadingComposition.navigation → Events.views → Events.candidates материализовал все 79 публикаций, хотя готовых событий нет: 12036 SQL statements, 110.39 ms.
- Карточки одного фида материализовали все 79 публикаций дважды и только после этого выбирали нужные 26: 21021 SQL statements, 187.34 ms.
- Subscriptions.effective для одного фида каждый раз загружал настройки истории всех фидов.
- Runtime._sync_sources при отсутствии активного задания вызывается после каждого sleep(0.02); prepare_youtube собирал полное представление всех материалов для проверки только статуса субтитров. Даже после оптимизации effective этот проход занимал 66.23 ms / 6570 SQL statements.
- Раз в 0.5 s processing.sync выполнял полный обход. Его прежняя стоимость — 146.09 ms / 17816 SQL statements. Это локальное согласование, а не запрос к внешнему обработчику.

Измерения SQL trace и cProfile выполнялись отдельно; числа выше относятся к wall time с SQL trace, а не к более дорогому профилирующему запуску. Реальные персональные данные не использовались как автоматические тестовые fixtures и не изменялись профилировщиком.

## Изменения

- Не вычислять кандидатов событий для reader views, пока нет принятого результата или сохранённого результата во время явного retry.
- Отбирать простые карточки по папке/фиду до чтения содержимого; представления событий и обзоров сохраняют прежнее правило принадлежности.
- При вычислении effective для одного фида загружать только его запись, сохраняя прежние поля history/settings.
- Для допуска YouTube читать первую строку сохранённого Markdown (JSON-заголовок) и заранее исключать уже ожидающие/выполняющиеся fetch.
- Исключать ожидающие/выполняющиеся fetch из event candidates до материализации, как прежде делала проверка material.loading.

## Фактическая проверка

После правок на той же копии:

| Операция | Время | SQL statements |
| --- | ---: | ---: |
| Навигация | 3.72 ms | 884 |
| 26 карточек выбранного фида | 36.72 ms | 2861 |
| Согласование обработки | 81.97 ms | 7741 |
| Подготовка YouTube | 1.87 ms | 81 |

Playwright: 20 переключений между двумя фидами по 26 карточек на копии текущих 79 материалов. Замер от DOM click до requestAnimationFrame после появления карточек. Медиана 81 ms, p95 155 ms, максимум 175 ms. Замер выполнен через production HTTP/UI; внешние источники и реальные обработчики к копии не подключались, обычное периодическое согласование работало. Скриншот просмотрен. Это не live-замер обновлённого пользовательского сервера: его Python-процесс не перезапускался.

Синтетическая HTTP-регрессия `tests/reader/test_reading_cost.py`: 4 YouTube-фида по 26 материалов; ограничивает число SQL statements независимо от скорости машины и проверяет результаты навигации и выбора фида. До правки FAIL: 12113 statements при бюджете 2000 на навигацию. После правки, вместе с `tests/reader/test_navigation.py`: 5 PASS.

Независимое ревью серверного diff: actionable findings отсутствуют; проверены retry событий, loading, заголовки материалов и сохранение history/settings.

Серверные правки применяются после следующего запуска Око. Предыдущая браузерная правка остаётся отдельным уже проверенным изменением.

## Final isolated verification

Isolated checkout based on `3e519b1`, prepared with task_changes.py and only the five server-task files. Command: `OKO_PYTHON=/Users/dabondarev/repo_personal/oko/.venv/bin/python ./scripts/check build python types`.

- contracts PASS; strict mypy PASS (54 source files); build PASS, including TypeScript and generated API checks.
- Full Python suite: **436 passed**, one httpx TestClient deprecation warning, 350.64 s. Checkout guard PASS.
- [Report](../check-runs/20261015-latency-isolated/report.json), [log](../check-runs/20261015-latency-isolated/output.log), [inspected screenshot](../check-runs/20261015-latency-isolated/feed-latency.png).
- No personal sources or real AI processors were started in tests. The temporary benchmark server was stopped; the copied personal dataset and helper scripts were removed.
