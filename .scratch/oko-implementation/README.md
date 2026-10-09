# Око: задачи реализации первой версии

Разбиение из 13 задач согласовано пользователем 2026-10-08. Тикеты хранятся локально отдельно от завершённых решений wayfinder.

Источник требований — [спецификация](../oko-product/spec.md). Она не копируется и не изменяется при публикации тикетов.

Каждая задача — законченный пользовательский сценарий с проверками. `ready-for-agent` означает подготовленность описания; начинать можно только после завершения всех `Blocked by`. Завершены «Первый путь читателя», «Подписки и настройки папок» и «Чтение RSS-статей». Telegram реализован, его приёмка на реальном аккаунте ожидает свободную существующую сессию (`integration-pending`). Локальные тезисы: done; настоящая модель Llama.app проверена через API Око и UI A на синтетических данных, включая отмену и удаление. См. docs/verification/llamacpp-theses.md. Codex: done; real synthetic API/browser integration verified, sandbox and fresh quota checked before source transfer. See docs/verification/codex-theses.md. По указанию пользователя №07 начат без завершения реальной приёмки №06; Ollama не запускать. Текущий локальный обработчик — llama.cpp. №08 YouTube реализован, настоящая проверка источника/субтитров ожидает доступность сети (integration-pending); начат по указанию пользователя без ожидания загрузки модели №06. Синхронизация Telegram зависит от завершения приёмки тикета 04. Прежде чем выполнять очередную, проверить фактические статусы блокеров; отметить её взятой в работу, после проверки — завершённой.

## Порядок и зависимости

| Задача | Блокирующие задачи |
| --- | --- |
| [Первый путь читателя](issues/01-first-reader-path.md) | Нет |
| [Подписки и настройки папок](issues/02-subscriptions-settings.md) | Первый путь читателя |
| [Чтение RSS-статей](issues/03-rss-reading.md) | Подписки и настройки папок |
| [Чтение Telegram с медиа](issues/04-telegram-reading.md) | Подписки и настройки папок |
| [Синхронизация прочитанного Telegram](issues/05-telegram-read-sync.md) | Чтение Telegram с медиа |
| [Тезисы через локальную модель](issues/06-ollama-theses.md) | Подписки и настройки папок |
| [Тезисы через Codex](issues/07-codex-theses.md) | Тезисы через локальную модель |
| [Обновления и тезисы YouTube](issues/08-youtube-updates.md) | Тезисы через локальную модель |
| [Фильтрация и возврат скрытого](issues/09-content-filters.md) | Тезисы через локальную модель |
| [Объединение новостей в события](issues/10-event-cards.md) | Фильтрация и возврат скрытого |
| [Обзоры и сжатие истории](issues/11-digests-compression.md) | Объединение новостей в события |
| [Завершение разбора папки](issues/12-folder-completion.md) | Обзоры и сжатие истории |
| [Правки и удаления в источниках](issues/13-source-revisions.md) | Чтение RSS-статей, Чтение Telegram с медиа, Обновления и тезисы YouTube, Обзоры и сжатие истории |

## Работа с независимыми ветвями

После подписок доступны RSS, Telegram и локальные тезисы. После локальных тезисов доступны Codex, YouTube и фильтрация. Зависимости обозначают готовность поведения и контрактов, а не разрешение бесконтрольно редактировать общие файлы одновременно.

Проверки удаления и остановки входят в каждую задачу. Наличие непроверенных реальных подключений должно быть явно указано при сдаче. Перед объявлением всей версии готовой проверить критерии всей спецификации на интегрированном приложении, включая сочетание Codex со смешанными сводками и Telegram-подтверждений с массовым просмотром. Отдельная задача не отменяет критерии остальных.

## Покрытие критериев спецификации

| Критерий | Задачи, расширяющие проверку |
| --- | --- |
| A01 | [Подписки и настройки папок](issues/02-subscriptions-settings.md) |
| A02 | [Подписки и настройки папок](issues/02-subscriptions-settings.md), [Чтение RSS-статей](issues/03-rss-reading.md), [Чтение Telegram с медиа](issues/04-telegram-reading.md), [Обновления и тезисы YouTube](issues/08-youtube-updates.md) |
| A03 | [Чтение RSS-статей](issues/03-rss-reading.md), [Чтение Telegram с медиа](issues/04-telegram-reading.md), [Обновления и тезисы YouTube](issues/08-youtube-updates.md) |
| A04 | [Чтение Telegram с медиа](issues/04-telegram-reading.md), [Синхронизация прочитанного Telegram](issues/05-telegram-read-sync.md) |
| A05 | [Синхронизация прочитанного Telegram](issues/05-telegram-read-sync.md) |
| A06 | [Чтение RSS-статей](issues/03-rss-reading.md), [Обновления и тезисы YouTube](issues/08-youtube-updates.md) |
| A07 | [Правки и удаления в источниках](issues/13-source-revisions.md) |
| A08 | [Фильтрация и возврат скрытого](issues/09-content-filters.md) |
| A09 | [Объединение новостей в события](issues/10-event-cards.md) |
| A10 | [Объединение новостей в события](issues/10-event-cards.md), [Правки и удаления в источниках](issues/13-source-revisions.md) |
| A11 | [Подписки и настройки папок](issues/02-subscriptions-settings.md), [Обзоры и сжатие истории](issues/11-digests-compression.md) |
| A12 | [Обзоры и сжатие истории](issues/11-digests-compression.md) |
| A13 | [Обзоры и сжатие истории](issues/11-digests-compression.md) |
| A14 | [Обзоры и сжатие истории](issues/11-digests-compression.md), [Завершение разбора папки](issues/12-folder-completion.md) |
| A15 | [Первый путь читателя](issues/01-first-reader-path.md), [Объединение новостей в события](issues/10-event-cards.md) |
| A16 | [Завершение разбора папки](issues/12-folder-completion.md) |
| A17 | [Подписки и настройки папок](issues/02-subscriptions-settings.md), [Фильтрация и возврат скрытого](issues/09-content-filters.md), [Объединение новостей в события](issues/10-event-cards.md), [Обзоры и сжатие истории](issues/11-digests-compression.md) |
| A18 | [Тезисы через локальную модель](issues/06-ollama-theses.md), [Тезисы через Codex](issues/07-codex-theses.md), [Фильтрация и возврат скрытого](issues/09-content-filters.md), [Объединение новостей в события](issues/10-event-cards.md), [Обзоры и сжатие истории](issues/11-digests-compression.md) |
| A19 | [Тезисы через локальную модель](issues/06-ollama-theses.md), [Тезисы через Codex](issues/07-codex-theses.md), [Обновления и тезисы YouTube](issues/08-youtube-updates.md), [Фильтрация и возврат скрытого](issues/09-content-filters.md), [Обзоры и сжатие истории](issues/11-digests-compression.md) |
| A20 | [Тезисы через локальную модель](issues/06-ollama-theses.md), [Тезисы через Codex](issues/07-codex-theses.md), [Обзоры и сжатие истории](issues/11-digests-compression.md), [Правки и удаления в источниках](issues/13-source-revisions.md) |
| A21 | [Синхронизация прочитанного Telegram](issues/05-telegram-read-sync.md) |
| A22 | [Подписки и настройки папок](issues/02-subscriptions-settings.md), [Синхронизация прочитанного Telegram](issues/05-telegram-read-sync.md) |
| A23 | [Синхронизация прочитанного Telegram](issues/05-telegram-read-sync.md), [Завершение разбора папки](issues/12-folder-completion.md) |
| A24 | [Первый путь читателя](issues/01-first-reader-path.md), [Чтение RSS-статей](issues/03-rss-reading.md), [Чтение Telegram с медиа](issues/04-telegram-reading.md) |
| A25 | [Первый путь читателя](issues/01-first-reader-path.md), [Завершение разбора папки](issues/12-folder-completion.md) |
| A26 | [Первый путь читателя](issues/01-first-reader-path.md), [Чтение RSS-статей](issues/03-rss-reading.md), [Чтение Telegram с медиа](issues/04-telegram-reading.md), [Тезисы через локальную модель](issues/06-ollama-theses.md), [Тезисы через Codex](issues/07-codex-theses.md), [Обновления и тезисы YouTube](issues/08-youtube-updates.md), [Фильтрация и возврат скрытого](issues/09-content-filters.md), [Объединение новостей в события](issues/10-event-cards.md), [Обзоры и сжатие истории](issues/11-digests-compression.md), [Завершение разбора папки](issues/12-folder-completion.md), [Правки и удаления в источниках](issues/13-source-revisions.md) |
| A27 | [Первый путь читателя](issues/01-first-reader-path.md), [Чтение RSS-статей](issues/03-rss-reading.md), [Чтение Telegram с медиа](issues/04-telegram-reading.md), [Объединение новостей в события](issues/10-event-cards.md), [Правки и удаления в источниках](issues/13-source-revisions.md) |
| A28 | [Первый путь читателя](issues/01-first-reader-path.md), [Чтение RSS-статей](issues/03-rss-reading.md), [Чтение Telegram с медиа](issues/04-telegram-reading.md), [Тезисы через локальную модель](issues/06-ollama-theses.md), [Тезисы через Codex](issues/07-codex-theses.md), [Обновления и тезисы YouTube](issues/08-youtube-updates.md), [Фильтрация и возврат скрытого](issues/09-content-filters.md), [Объединение новостей в события](issues/10-event-cards.md), [Обзоры и сжатие истории](issues/11-digests-compression.md), [Завершение разбора папки](issues/12-folder-completion.md), [Правки и удаления в источниках](issues/13-source-revisions.md) |
| A29 | [Первый путь читателя](issues/01-first-reader-path.md) |
| A30 | [Первый путь читателя](issues/01-first-reader-path.md), [Тезисы через локальную модель](issues/06-ollama-theses.md), [Тезисы через Codex](issues/07-codex-theses.md) |
| A31 | [Первый путь читателя](issues/01-first-reader-path.md), [Завершение разбора папки](issues/12-folder-completion.md) |
| A32 | [Чтение RSS-статей](issues/03-rss-reading.md), [Чтение Telegram с медиа](issues/04-telegram-reading.md), [Обновления и тезисы YouTube](issues/08-youtube-updates.md) |
