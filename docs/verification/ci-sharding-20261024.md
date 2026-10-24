# CI sharding — проверки и замеры 2026-10-24

Задание: два Python-шарда, два браузерных, отдельная latency job;
актуальный main, проверки и фактические before/after, рабочее Око не трогать.
База: `1046c04cb3929fbae351263f84ac80c66ae06ebb`.
Ветка: `codex/ci-sharding`, отдельный managed worktree.

## Что изменено

- Build создаёт один manifest: revision, SHA256 файлов, точный inventory,
  непересекающиеся и непустые python-1/2 и browser-1/2, отдельный latency.
- Назначение по медиане длительностей; cache miss даёт прежний size fallback.
  Каждый шард запускает существующий runner с прежними caps4 и единым планом.
  Локальные all/ui/browser, явные --files и прежний последовательный latency сохранены.
- CI использует семь параллельных jobs: Python2, browser2, latency1, Node1, tools1.
  Assets строятся один раз и проверяются после скачивания. Node/tools не шардированы,
  выбор tools по изменённым входам сохранён. Имена report artifacts уникальны.
- Full checks ждёт успеха build и всей матрицы, затем требует один валидный отчёт
  на каждую часть: plan id, clean checkout, непустые counts без errors/failures,
  фактические file_durations и selected_files должны совпасть с планом без дублей.
  Latency обязана выполнить все4 случая; 320 кликов и p95<200мс прежние.
- Только общий успешный аудит обучает единый timing cache. Частичные шарды не
  обучаются; cache остаётся advisory. Первый CI может взять старые unsharded hints.

## Выполненные локальные проверки

- Red до реализации: новый planner CLI отсутствует, проверка partition FAIL.
- Первые planner/gate проверки:12 passed; runner/timings/planner:80 passed за24.08с.
- Расширенный набор:36 passed/1 failed за15.69с — новая регрессия ожидала внутренний
  parallel plan даже у однофайлового шарда. Уточнено ожидание: одиночный файл штатно
  выполняется без fork-планирования; многосоставный шард обязан использовать hints.
- Повтор planner/timings/snapshot/feedback:41 passed за15.30с. Включены реальные
  Python-процессы двух частей, signed-plan tampering, пропуски, повторы, чужие,
  грязные и failed reports, zero counts, неизменность истории после failed audit.
- Независимое read-only ревью всего diff: без замечаний. Reviewer тесты не запускал.
- Официальные action.yml подтверждают cache-matched-key для bootstrap и отдельные
  artifact directories при download pattern без merge-multiple.

## Метод before/after

Before: успешный [Checks205](https://github.com/tgenman/oko/actions/runs/40119600389),
head b40d49f, дерево кода совпадает с squash1046c04c. Весь интервал от первого
runner log build до последнего log Full checks —316.921с (5:17).
Включает setup, передачу assets/artifacts и межгрупповые задержки. Ожидание первого
runner до начала build в эту метрику не входит. Дополнительно Checks203 —328.636с.

Before205: build51.986с, Python203.812с (тесты176.822с), browser252.916с
(функциональные172.929с + latency45.233с), tools76.459с, Node23.366с.
Сумма elapsed всех job logs —608.850с (10.15 машино-минут), без округления биллинга.
Это4CPU Linux runners, четыре Python/browser исполнителя на VM.

After будет измерен тем же способом через фактический draft PR CI. Новые проверки
самого шардинга увеличивают tools inventory; исходные Python911, browser225+2skip
и Node113 сохраняются. Число кейсов внутри частей не обязано быть одинаковым:
балансируется измеренное время. Это сравнение на shared GitHub-hosted runners,
не статистическая гарантия процента ускорения. Неполные/failed прогоны не считаются
зелёными замерами. Raw reports/logs хранятся во временных каталогах и CI artifacts;
компактные численные результаты будут приложены к отчёту.

Рабочая установка, реальные подписки, Telegram, пользовательские данные и настоящий
AI не используются. Draft PR нужен для реального CI-замера; слияние не выполняется.

## Полная локальная проверка

Frozen `./scripts/check all tools --browser-workers 8 --compact`: **PASS,200.11с**.
Python/HTTP911, tools264, Node113, Playwright225 passed/2 прежних skipped.
Contracts/types/syntax/lint/build/HTTP/entrypoint/diff и checkout guard PASS.
Test tree `b3af41b449260380ba0682ce6f57a83988229770`.
Отчёт: `/private/tmp/oko-ci-sharding-local-20261024/20261024T184302Z-84330c2d/report.json`.
После старта snapshot менялись только этот отчёт и компактные measurements.json.
Локальный all не распределяет тесты по разным VM; реальный sharding измеряется в CI.
