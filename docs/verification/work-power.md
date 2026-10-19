# Включение и выключение Око — фактическая проверка

Дата: 2026-10-19. Задача: https://github.com/tgenman/oko/issues/47.
Спецификация: [.scratch/work-power/spec.md](../../.scratch/work-power/spec.md).
Ветка: `codex/oko-power`. База ревью: `026aaf13100b91d8d4a06820ebe5eb8423634f4d`.
Реализация: `7bc7304`, определение в глоссарии: `1bb4e69`, исправления ревью: `fe818b4`.

## Выполнено

HTTP: временные SQLite/файлы и синтетические Source, Telegram, Codex, HTTP transports.
Браузер: Playwright + headless Google Chrome; личные источники и настоящий обработчик
не запускались. Проверочные checkout изолированы от посторонних изменений.

1. TDD: первая HTTP-проверка получила 404 новой команды; после реализации прошла.
   Проверка запретов сначала получила прежний общий 400, затем подтвердила типизированный
   409 runtime_disabled во всех выбранных командах. Браузерный сценарий после исправления
   адреса синтетической фикстуры и селектора сначала воспроизвёл отсутствие Start,
   после реализации прошёл.
2. Целевые проверки до ревью: power + runtime actions — 6 passed; затем power — 4 passed,
   API contract + power — 6 passed; power + opened material в браузере — 11 passed.
   TypeScript всех проектов прошёл. Скриншот выключенного читателя просмотрен:
   Start на прежнем месте, материал/изображение доступны, обновление и добавление недоступны.
3. Первый `./scripts/check all --in-place --compact` в изолированном checkout:
   FAIL build, 16.44 s. При сокращении diff OpenAPI было потеряно тело PUT конфигурации
   Codex. HTTP/Python/browser/tools в этом запуске NOT RUN. Контракт исправлен;
   отдельно сверены тела, параметры и метаданные всех прежних операций с базой.
4. Полный повтор на исправленном снимке — PASS, 133.18 s:
   contracts, tooling types, syntax, lint, Python types, browser types, build,
   HTTP 9 passed, Python 472 passed, tools 135 passed,
   browser 163 total: 161 passed, 2 skipped, 0 failed; diff и entrypoint PASS.
5. Независимое ревью: Standards — 0 замечаний; Spec — 2 P2, оба исправлены:
   допуск Telegram web preview повторно проверяется под action lock;
   отмена identity check не портит состояние подтверждённого профиля.
   Оба дефекта сначала воспроизведены новыми HTTP-регрессиями (RED), после исправлений
   обе проверки прошли (GREEN). Повторное независимое Standards/Spec ревью — 0 замечаний.
6. После `fe818b4` выполнен целевой runner в изолированном checkout:
   `./scripts/check python types --files tests/lifecycle/test_power.py tests/lifecycle/test_runtime_actions.py tests/reader/test_telegram_bindings.py tests/reader/test_telegram_read_sync.py tests/reader/test_telegram_web.py tests/reader/test_v3_telegram.py --compact`.
   PASS contracts, types и 64 Python tests, 10.01 s. Проверены отмена, две гонки подключения,
   восстановление, запреты, локальное чтение и отложенные Telegram-отметки.
   UI после ревью не менялся; успешный полный браузерный прогон сохранён как его доказательство.
7. Финальная `npm run build` в рабочем checkout — PASS; generated API актуален,
   типизация и Vite прошли. Пользовательский процесс не перезапускался.

Полный all относится к снимку до двух исправлений Telegram; после них выполнен
указанный целевой набор, полный all повторно не запускался.

## Ограничения и артефакты

Два прежних браузерных сценария отключены по запросу пользователя 2026-10-15:
coexistence/BFCache и closing/keepalive. Их результат — skipped, не PASS.
Живые подписки, реальные AI/Telegram и пользовательское развёртывание не проверялись.

Локальные сохранённые отчёты и логи:
`.scratch/check-runs/work-power-20261019/{initial,full,followup}/`;
скриншот: `.scratch/check-runs/work-power-20261019/power-disabled.png`.
Точные исходные отчёты:
- `/private/tmp/oko-power-check-01/.scratch/check-runs/20261019T130207Z-93213f66/report.json`
- `/private/tmp/oko-power-check-02/.scratch/check-runs/20261019T130350Z-9800f284/report.json`
- `/private/tmp/oko-power-check-02/.scratch/check-runs/20261019T130812Z-68ea22f9/report.json`

Прототип сохранён в локальной ветке `codex/power-prototype-20261019`, коммит
`c1faa9ee9695398834f822cf0091178766b4d798`; throwaway HTML отсутствует в рабочем frontend.
Посторонний термин глоссария, соседний прототип обновления, backup и документ о Docker
не включены в коммиты этой задачи. Коммиты реализации и архивная ветка пока локальные.
