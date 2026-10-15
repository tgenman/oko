# Интеграция ветки ускорения с main — 2026-10-15

В ветку codex/faster-python-browser-checks влита origin/main e0461da (merge d0813fd). Сохранены новые подключения v3 и helper scripts/legacy_api.py, исключающий снятые OPML paths/DTO. Helper включён в API cache inputs и материализацию точного Git index. Approval/candidate-процедуры остаются удалёнными.

Свежая проверка `generate-api.mts check --fresh` прошла после слияния. Она выполнена отдельно от итогового полного замера.

После merge первый `check all tools` занял 105,26 с и упал в test_factory_default_empty_history_completes_without_a_real_history_worker: v3 subscribe уже запускал date-job, а тест повторно запускал count-job. Итоговая адаптация подключает источник через v3 сразу с count=3 и проверяет mode=count/state=done, loaded=0 и отсутствие карточек. Все 10 тестов модуля прошли.

Следующий полный прогон: 95,68 с, FAIL test_reader_gets_captions_through_owned_worker. Состояние показывало deferred captions и истёкшую клиентскую сессию. Убрана ненужная смена importance после подключения, которая могла отменить уже запущенный worker и включить десятисекундный admission delay. Default theses=True уже запрашивает captions. Проверка настоящего дочернего worker, полученного текста и удаления материала сохранена; весь YouTube-модуль (13 тестов) PASS.

Итоговый `./scripts/check all tools` на e686b0c: **PASS, 91,25 с**. Использовался кэш типов и готовой frontend-сборки с проверкой целостности; этот замер не является новым чистым сравнением с историческими результатами.

- Python: 346 passed, 83,55 с.
- HTTP: 9 passed.
- Browser: 117 passed, 2 ранее отключённых skipped, 58,76 с. Состав изменился после слияния main.
- Tools: 111 passed, 79,17 с.
- Contracts, синтаксис, lint, типы, build, diff, entrypoint: PASS.

Независимое ревью интеграции и обеих адаптаций не выявило оставшихся замечаний. После полного прогона добавлен только этот отчёт; `git diff --check` PASS.

Отчёты: `/tmp/oko-pr-after-main/20261015T172739Z-5516563b/report.json` (FAIL), `/tmp/oko-pr-final/20261015T173120Z-88923656/report.json` (FAIL), `/tmp/oko-pr-verified/20261015T173450Z-7a669dad/report.json` (PASS). Свежая генерация: `/tmp/oko-pr-api-fresh.log`.
