# Пакеты Око и организация тестов

2026-10-09. Согласованы кандидаты 1 и 2 обзора структуры: пакеты по ответственности, общий контракт обработки отдельно от adapters и независимая поддержка тестов. Исходная база: `b8a721a`. План реализован; устройство и проверки описаны ниже.

## Границы

Сохранить поведение читателя, команды запуска, формат SQLite/Markdown, правила разрешения публичного AI, владение процессами, отмену и восстановление. Не добавлять новые функции, framework, второй adapter хранения или промежуточные modules только для реэкспорта.

Сохранить решения [управления работой](reader-work.md) и [прогресса обзора](digest-progress.md): Runtime владеет работой Око; Store — фиксацией и восстановлением; Digests — этапами обзора. Пересмотр связанности Store и обзоров — отдельная задача. В частности, тезисы пока остаются внутри implementation Store.

## Код

```text
oko/
  __init__.py
  __main__.py
  reader/
    __init__.py
    app.py
    runtime.py
  collection/
    __init__.py
    contracts.py
    demo.py
    rss.py
    telegram/
      __init__.py
      source.py
      client.py
      worker.py
    youtube/
      __init__.py
      source.py
      worker.py
  processing/
    __init__.py
    contracts.py
    llamacpp.py
    codex.py
  reviews/
    __init__.py
    filtering.py
    events.py
    digests.py
  storage/
    __init__.py
    store.py
  subscriptions/
    __init__.py
    subscriptions.py
  web/
    app.js
    style.css
```

| Сейчас | После |
| --- | --- |
| `oko/app.py`, `oko/runtime.py` | `oko/reader/app.py`, `oko/reader/runtime.py` |
| `oko/sources.py` | `oko/collection/contracts.py` и `demo.py` |
| `oko/rss.py` | `oko/collection/rss.py` |
| `oko/telegram.py` | `oko/collection/telegram/source.py` |
| `oko/telegram_client.py`, `oko/telegram_worker.py` | `oko/collection/telegram/client.py`, `worker.py` |
| `oko/youtube.py`, `oko/youtube_worker.py` | `oko/collection/youtube/source.py`, `worker.py` |
| `oko/processing.py` | `oko/processing/contracts.py` и `llamacpp.py` |
| `oko/codex.py` | `oko/processing/codex.py` |
| `oko/filtering.py`, `oko/events.py`, `oko/digests.py` | соответствующие файлы `oko/reviews/` |
| `oko/store.py` | `oko/storage/store.py` |
| `oko/subscriptions.py` | `oko/subscriptions/subscriptions.py` |

`__main__.py` остаётся точкой сборки запуска. Каталог `web` остаётся рядом с пакетами; перенос Python-файлов требует исправления его разрешения в app.py. Пустые `__init__.py` обозначают пакеты, а не создают дополнительные interfaces.

Общий Source interface, типы материалов и ошибка сбора находятся в `collection/contracts.py`; конкретный RSS adapter не владеет ошибкой, которую использует Telegram. Demo — отдельный синтетический adapter того же seam. Клиент и worker находятся рядом с соответствующим источником.

Общие входы, результаты, атрибуция, инструкции и ошибки обработки находятся в `processing/contracts.py`. Настройка подключения и проверка адреса llama.cpp относятся к `llamacpp.py`. Codex и llama.cpp adapters зависят от общего контракта; общий контракт не импортирует их. Существующие interfaces сохраняются, новый универсальный обработчик не вводится.

Locality улучшается размещением связанных implementation рядом. Leverage общего контракта обработки сохраняется для двух реальных adapters. Перенос по папкам сам по себе не увеличивает depth. Deletion test: удаление общего контракта размножит правила атрибуции между adapters; удаление папок изменит только навигацию.

## Тесты

```text
tests/
  __init__.py
  process_server.py
  support/
    __init__.py
    reader.py
    models.py
    sources.py
    telegram.py
    processes.py
  reader/
    test_reader.py
    test_security.py
    test_subscriptions.py
    test_folder_completion.py
    test_deletion.py
  collection/
    test_rss.py
    test_telegram.py
    test_youtube.py
  processing/
    test_theses.py
    test_theses_recovery.py
    test_codex.py
  reviews/
    test_filters.py
    test_events.py
    test_digests.py
    test_digest_recovery.py
  lifecycle/
    test_lifecycle.py
    test_runtime_actions.py
    test_process.py
```

Добавить пустые `__init__.py` в тестовые пакеты, чтобы импорты поддержки не зависели от другого установленного пакета tests. `tests.process_server` остаётся стабильной точкой процессных проверок.

Перенести общие обычные помощники из test-файлов:

- `HEADERS`, `wait_for`, `setup_reader` — в `support/reader.py`.
- Синтетический model transport — в `support/models.py`.
- Повторно используемый `EventSource` — в `support/sources.py`.
- Синтетический Telegram adapter и помощник подключения — в `support/telegram.py`.
- Запуск, подключение, ожидание карточек и очистка принадлежащего тесту процесса — в `support/processes.py`.

Поддержка не импортирует `test_*`. Тестовые сценарии также не импортируют другие `test_*`. Помощники, используемые только одним сценарием, остаются рядом с ним. В conftest помещать только реальные pytest fixtures, если они понадобятся; файл не создавать ради структуры.

Сначала отделить общую поддержку, затем разделить проверки тезисов и обзоров: основные сценарии отдельно от остановки, перезапуска, повреждённого прогресса и восстановления. Сохранить parametrization, утверждения и настройки сценариев. Не дробить тесты по отдельным assertions и не заводить общий изменяемый client между тестами.

Interface читателя остаётся основной test surface с настоящими временными SQLite/файлами и заменяемыми внешними adapters. Locality подготовки и очистки сосредоточена в support; leverage — повторное использование в нескольких наборах. Deletion test: удаление support размножит эти правила между сценарными modules. Проверки private-методов вместо существующих сценариев не нужны.

## Порядок переноса

1. Записать список собранных тестов и проверить исходное состояние. Учесть изменение node IDs при переносе и разделении файлов.
2. Вынести тестовую поддержку, убрать зависимости test→test; проверить затронутые наборы.
3. Перенести collection и processing, обновить все импорты и процессные точки запуска. Общие контракты не зависят от конкретных adapters.
4. Перенести reader, reviews, storage и subscriptions, обновить точку сборки и разрешение web.
5. Сгруппировать тесты, разделить основные сценарии и восстановление, обновить точные команды в README и действующих документах запуска. Исторические отчёты приёмки не переписывать как новые результаты.
6. Выполнить итоговые проверки и ревью Standards/Spec; зафиксировать фактический результат отдельно от этого плана.

## Приёмка

- Состав собранных тестовых случаев не потерян: сравнить имена сценариев и parametrization, учитывая новые пути. Текущая база — 161 прошедший случай по предыдущему отчёту, а не результат этой архитектурной сессии.
- Все существующие сценарии проходят; mypy проверяет весь `oko`, без новых исключений строгой типизации.
- Нет импортов `tests.test_*` или импортов одного сценарного набора из другого; нет оставшихся импортов старых production-путей.
- `python -m oko --help` и запуск Demo работают; читатель выдаёт HTML, JavaScript и CSS из сохранённого `oko/web`.
- Обновлены `-m oko.telegram_worker`, `-m oko.youtube_worker` и вычисление cwd. Проверки действительно проходят путь запуска worker с синтетическими данными, а не только импортируют его. Не запускать пользовательский Telegram-аккаунт или модель для проверки переноса файлов.
- `-m tests.process_server` продолжает работать; проверки остановки, аварийного завершения, удаления и позднего ответа сохраняют свои сценарии.
- Не меняются схема/формат хранения, точки fault injection, настройки, ограничения публичного AI и управление пользовательскими процессами.

## Следующий шаг

Реализовать этот рефакторинг по документу `docs/architecture/package-layout.md`. Согласование структуры завершено; дополнительные продуктовые решения не требуются.

## Реализация

Перенос выполнен по согласованной структуре. `tests/support` дополнительно содержит `codex.py`, `rss.py`, `youtube.py`, `filters.py`, `events.py` и `digests.py`: они сосредоточивают уже повторно используемые adapters и подготовку отдельных сценариев, не возвращая импорты test→test. Новый `conftest.py` не понадобился.

Общие правила обработки находятся в `processing/contracts.py`, подключение llama.cpp — в `processing/llamacpp.py`; ошибка сбора перенесена из RSS в `collection/contracts.py`. Тезисы остаются в Store. Старые плоские пути заменены прямыми импортами новых modules без реэкспортов. Запуск `python -m oko` и `tests.process_server` сохраняется.

Проверки и ревью: [отчёт](../verification/package-layout.md).
