# Фактическая проверка — 2026-10-22

Реализован A/A: карточка document перед текстом, общий значок документа на превью
списка. Имя/size_label сохраняются, размер не выдаётся за автора или измеренные
локальные байты. Метаданные document wrapper удалены из текста поста;
без подписи заголовок берётся из имени, включая пост-ответ.

Публичный embed https://t.me/lexkravetski/861?embed=1&mode=tme прочитан через HTTP:
структура document_wrap/title/extra подтверждена, extra = 36.2 KB,
href = ссылка на сообщение. Содержимое файла не скачивалось.

## Запуски

- `npm run api:generate` — PASS, canonical OpenAPI и обе библиотеки обновлены.
- `npx tsc --noEmit` — PASS.
- mypy трёх затронутых Python-модулей — PASS.
- `npm run build` — PASS, включая проверку API и типов.
- pytest `tests/reader/test_external_telegram_media.py`: первый запуск
  14 PASS / 1 FAIL из-за ожидания одной карточки в новом тесте с двумя постами;
  исправлен аргумент count; повтор 15 PASS за 8.19 с.
- pytest `tests/reader/test_telegram_web.py tests/api/test_contract.py`:
  18 PASS за 17.61 с.
- `./scripts/check browser --files tests/browser/test_external_telegram_media.ts
  tests/browser/test_telegram_post_unavailable.ts`: 4 PASS, browser/tooling types PASS.
  Отчёт: `.scratch/check-runs/20261022T062157Z-794db42d/report.json`.
  Скриншот `artifacts/browser/58975/external-document-desktop.png` просмотрен:
  плашка файла/размер/пояснение и значок списка соответствуют A/A.
- Независимый reviewer обнаружил неправильный fallback заголовка документа-ответа;
  исправлено и добавлен сценарий в HTTP-тест. Целевой повтор 1 PASS за 1.42 с;
  reviewer подтвердил закрытие замечания.
- `git diff --check` — PASS.

Проверки используют временные хранилища, синтетические источники и HTTP читателя.
Полный all не запускался. Не выполнялись реальные скачивания Telegram-файлов,
изменения личных подписок, commit/merge/push реализации или обновление рабочего
экземпляра. Ранее сохранённый прототип доступен в архивной ветке
`codex/telegram-file-prototype-20261021` на `9148f8fbad13ab7f9747a06091c7c95ad881301f`.
