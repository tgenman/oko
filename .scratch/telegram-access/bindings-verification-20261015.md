# Telegram #18: привязки, профили, claims и удаление

База `c401abc` (main после merge PR #23), ветка `codex/telegram-bindings-profiles`.
Issue: https://github.com/tgenman/oko/issues/18. Применён implement: HTTP/SDK
seams с synthetic adapters, целевые прогоны, один полный прогон и две оси ревью.

## Реализованный объём

- На существующей SQLite Subscriptions добавлены неизменяемые bindings,
  profiles с user ID/generation, уникальные peer/username claims и все owners.
  Новые Telegram feed IDs случайны; существующие IDs, aliases и содержимое
  не переименовываются. Сопоставление legacy alias роли только явным PUT.
- Миграция локальна, транзакционна, без аккаунта. Несколько старых владельцев
  одного claim сохраняются; GET/duplicate показывает всех. Более двух aliases
  остаются в каталоге. Topic bindings вызывают отказ до любых schema/PRAGMA
  writes; HTTP сообщает необходимость ручной миграции, hash БД не меняется.
- Реализованы уже описанные canonical v3 profiles/connect/remove endpoints.
  Подключение проверяется вне actions, фиксация сверяет epoch/generation и
  claims в одной транзакции. Duplicate не делает upsert настроек/истории.
  Аккаунты подключаются через ClientRegistry, без секретов в Store/API.
- Worker получает ожидаемый user ID и делает get_me перед чтением, медиа и
  ack. При изменении .env LocalTelegram перезапускает свой worker; состояние
  профиля после запуска требует новой проверки. Та же личность восстанавливается,
  иной аккаунт при зависимых фидах и одинаковая личность двух ролей отвергаются.
- Runtime использует явную TelegramBinding для namespace/read admission.
  Legacy client не допускается к сбору до подтверждения профиля. Отсутствует
  вывод способа доступа через bool(publication.members). Подтверждённые aliases
  дополняются при обычной проверке, противоречащий peer не объединяется.
- Delete сначала закрывает допуск и поколения, затем использует существующие
  jobs удаления файлов. Финальная sweep-задача удаляет настройки, history,
  read state, jobs, tombstones и claims только после очистки всех материалов.
  Процессный restart продолжает cleanup; deleting виден в каталоге, duplicate
  держится до конца, re-add получает другой ID. Viewed/ack/leave не вызываются.
- Legacy HTTP не обходит подтверждение профиля или immutable transport.
  Изъяты старые продуктовые сценарии отдельного topic-feed; SDK scoped safety
  сохранена, sparse archive и поздний ack проверяются для всей группы/delete.

## Границы следующих задач

#19: per-account workers/lanes и безопасная whole-forum отметка. Существующий
worker по-прежнему запрещает whole-forum операции, которые не имеют безопасной
реализации; это не разрешение broad ack. #20: web discover/fetch, bounded
history envelope adoption, Telegraph/media. Здесь web делает только bounded
preview-проверку без аккаунта через httpx/BeautifulSoup (закреплены dependency
и lock), сохраняет web binding; веб-сбор ещё не включён. Код RSSHub не переносился.
#21/#5: переключение UI/оставшихся consumers на новые операции.

Canonical и generated API bytes не менялись; удалённые approval/candidate gates
не восстановлены. Личные источники/сессии и настоящие обработчики не запускались.
Свойства Telegram-сервера и доступность CDN synthetic SDK не доказывает.

## Фактические проверки до полного прогона

- Red: первый profiles GET получил 404; после реализации HTTP-профильный сценарий PASS.
- Red: v3 catalog ошибочно показывал новый client как legacy_client; подключена
  сохранённая binding projection. Проверка обеих схем показала неправильный
  старый error envelope web read setting; приведён к canonical typed error.
- SDK subprocess с настоящими request types: изменение user ID между запросами
  блокирует history, media и ack до вызова внешних операций.
- Целевой набор Telegram, read sync, история, lifecycle, существующая история:
  **70 passed, 23.37 s**. После явной binding adoption типовые исправления и
  расширенный новый HTTP-файл: **11 passed, 2.55 s**. Mypy всего oko: PASS.
- Первый запуск legacy-наборов не имел frontend build; после npm run build
  он заменён фактическими целевыми результатами. Промежуточные падения снятых
  topic/transport сценариев адаптированы без skip. Один промежуточный runner
  зафиксировал изменения checkout во время локальной работы — его результат
  не используется как доказательство. Новых skip нет.

Полный прогон и независимое ревью дополняются ниже после завершения.

## Итоговые проверки и исправления ревью

Первый полный `./scripts/check all tools` на 9328dc3: **FAIL, 131.00 s**.
Python 372 passed / 2 failed: старые setup в `test_source_disabled_switch...`
и `test_effective_fields_reset...` меняли Telegram transport через PATCH.
Проверка отключения источника переведена на обычный RSS, проверка прав и
наследования сохранена, отдельно проверен отказ смены Telegram transport.
Browser: 117 pass / 2 прежних skip; tools: 111 pass; HTTP 9 pass; прочие
наборы PASS. Отчёт: `.scratch/check-runs/20261015T190343Z-899cc313/report.json`.
После исправлений целевой runner PASS 8.67 s; новый HTTP-файл 14 pass.

Первое независимое Spec review выявило один P1: legacy adoption по совпадению
peer допускала другой alias аккаунта. Red HTTP regression воспроизвёл 200
вместо 409. Исправление сверяет исходный alias и сохранённый user ID до записи;
каталог и материал не меняются. Retired upsert в Subscriptions удалён; fixture
явно строит pre-upgrade SQL, а миграция проверяется через HTTP. Standards
review замечаний не имело; повторные обе оси подтвердили 0 оставшихся замечаний.

Дополнительно воспроизведён late discover после delete/re-add: SQLite повторно
выдаёт job ID, прежние id/generation могли совпасть. Добавлен сохраняемый
монотонный dispatch generation; перенос execution_settings сохраняет параметры
и signature retry, valid проверяет также kind/item. HTTP red/green подтверждает,
что старый ответ не создаёт карточку и не завершает новое задание. Старое
правило ID RSS/YouTube сохранено. Широкое изменение их ID было отклонено
автоматической проверкой как выход за #18 и не применялось; защита поколений
решает риск без него. Python+types после этой правки: **PASS, 41.70 s**,
387 tests (эта выборка также включает HTTP), mypy всего приложения PASS.
Отчёт: `.scratch/check-runs/20261015T191645Z-79cfb3b2/report.json`.

В ходе работы main продвинулся до `3f15488` (PR #24, uv). Он подлит merge
bbc0670: Poetry не возвращён, BeautifulSoup перенесён в uv.lock без обновления
остальных библиотек main. Создана отдельная .venv этого worktree через
`uv sync --locked --python python3.11`; пользовательская .venv не синхронизировалась.
Внутренний feed-delete job проецируется в существующий публичный delete kind:
HTTP red 500 во время cleanup исправлен на 200 и phase=deleting. Последний
HTTP-файл: **15 passed, 4.89 s**, mypy: PASS.

Повторный полный прогон обоснован новой базой, окружением и командами uv:
`./scripts/check all tools` на 5db41da — **PASS, 106.07 s, exit 0**.

- Python: **378 passed** (193 + 185).
- HTTP: **9 passed**.
- Browser: **117 passed**, 0 fail, **2 прежних skipped**.
- Tools: **111 passed** (49 + 62).
- Contracts, все типы, syntax, lint, build, diff, entrypoint: PASS.

Финальный JSON: `.scratch/check-runs/20261015T191956Z-c5b8cc24/report.json`.
Независимые targeted reviews исправления generation, merge uv и status fix:
**0 замечаний по Spec и Standards**. После полного прогона менялся только этот
отчёт; `git diff --check` PASS. Личная аккаунтная приёмка не проводилась.
