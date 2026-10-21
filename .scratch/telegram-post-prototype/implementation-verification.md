# Фактическая проверка: пост Telegram без веб-содержимого

Дата: 2026-10-21. Задача: https://github.com/tgenman/oko/issues/76.
Спецификация: [spec.md](spec.md). Ветка: `codex/telegram-post-web-unavailable`.
База ревью: `29cfc91fa535b1bee79267e7945df99c5d43fbaa`.
Код: `b8bf0ba`, правки `4c080ab` и `6c210bd`.

## Результат

Реализована карточка ссылки C и отдельный значок Telegram на превью фида/папки.
`SourceManifest.post_web_unavailable` сохраняется и передаётся отдельно от внешнего
аудио и видео с ограничением размера. Пустой неподтверждённый ответ не становится
штатной пометкой. Ограничение не создаёт проблему или цикл повторов, пустой пост
не поступает в ИИ. Ранее сохранённая копия остаётся доступной; реальные сбои,
включая потерю локального файла, не скрываются.

Уже полученное веб-окно позволяет переоценить известные посты без дополнительных
запросов и расширения истории. Появление текста допускает обычную загрузку;
уточнение прежней неизвестной заглушки не создаёт ложное «Изменено».

## Проверки и ограничения

Использованы временные SQLite/файлы, синтетические HTTP/AI adapters и настоящий
Playwright + установленный Chrome. Личные подписки/Telegram-профили и настоящий
ИИ не использовались. Computer Use не применялся.

- TDD: исходная заглушка создавала ложные проблемы; HTTP-тест сначала падал.
  Отдельные red → green проверки подтвердили удержание сохранённого текста и
  отсутствие ложного `changed` при переклассификации unknown → ограничение.
- Браузерный red → green: значок, карточка C, переход post → audio → video → post,
  отсутствие retry/ИИ-панелей, фокус ссылки, поздний ответ и историческая версия.
- `npm run build` прошёл; OpenAPI/Python/TypeScript библиотеки согласованы.
- Первый `check all` остановился на двух mypy-ошибках чтения атрибута class,
  до Python/браузерных наборов. Исправлено в `4c080ab`.
  Отчёт: `/private/tmp/oko-post-check-20261021/.scratch/check-runs/20261021T183935Z-af051314/report.json`.
- Повторный `./scripts/check all --compact` на дереве `4c080ab`:
  **707 Python PASS, 1 FAIL; 205 browser PASS, 2 skipped; 4 performance PASS;
  136 tool PASS**. Contracts/types/syntax/lint/build/http/diff/entrypoint PASS.
  Полный прогон **не считается зелёным**: YouTube-сценарий
  `test_future_live_is_discarded_but_other_unplayable_errors_remain[This video is not available in your country.-False]`
  не дождался состояния captions=error в своём сроке, получил ожидание загрузки.
  Причина задержки не установлена, код YouTube не менялся.
  Отчёт: `/private/tmp/oko-post-check-20261021/.scratch/check-runs/20261021T184048Z-7bda8338/report.json`.
- Отдельный повтор обеих параметризаций упавшего YouTube-сценария в той же
  собранной копии: **2 PASS**. Прямой пробный запуск из внешней проверочной копии
  без assets до этого не выполнил сценарий: frontend build отсутствовал;
  повтор был сделан в собранной копии.
- После последней правки `6c210bd`:
  `./scripts/check python types --files tests/reader/test_telegram_post_unavailable.py tests/reader/test_external_telegram_media.py tests/reader/test_reading_versions.py tests/collection/test_youtube.py::test_future_live_is_discarded_but_other_unplayable_errors_remain`
  — **40 PASS** (11 новых + 14 внешних медиа + 13 версий + 2 YouTube),
  contracts и mypy **PASS**. Проверялось отдельное дерево задачи, исключающее
  посторонние изменения checkout.
  Отчёт: `/private/tmp/oko-post-check-20261021/.scratch/check-runs/20261021T184706Z-fba728cb/report.json`.
  Этот результат относится к выбранным сценариям, не заменяет полный прогон.
- В промежуточном браузерном прогоне выявлено отсутствующее поле ReadingTextContent
  в OpenAPI; исправлено до полного прогона. Добавление третьего поста в прежнюю
  фикстуру также задерживало обложку; новая фикстура отделена, прежние сценарии
  аудио/видео вновь PASS. Их production-поведение не ослаблялось.
- Два независимых ревью: Standards — **0 actionable**, Spec — **0 actionable**.
  Обе последние правки перепроверены соответствующими reviewer-агентами.

## Визуальная проверка

Снимки карточки C просмотрены: [desktop](verification/telegram-whole-post-C.png),
[узкая карточка](verification/telegram-whole-post-C-narrow.png).
Ссылка и адрес переносятся внутри узкой карточки; это не утверждение об адаптивности
всего многоколоночного читателя.

## Сохранность и поставка

Throwaway-исходники A/B/C сохранены в локальной ветке
`codex/telegram-post-prototype-20261021`, коммит `d4c2a7b05f782511e9732802c1bde5fc3e95a503`,
и удалены из рабочего frontend. Production написан отдельно.
Посторонний прототип source-update, исследование Twitter и отчёт прежнего обновления
не включаются в коммиты задачи. PR, push, слияние и обновление рабочего экземпляра
в этом этапе не выполнялись.
