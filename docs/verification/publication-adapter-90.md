# Telegram Web через общий API адаптеров — #90

База: `3c113fa854a4aae6f36c2787ffe25572552faf32`. Реализация по #89/#90 в отдельном
checkout `/tmp/oko-pipeline-implementation`, ветка `codex/90-telegram-web-api`.
Личные источники, аккаунты и рабочая база не используются.

## Проверенные результаты

- TDD: начальный тест контракта упал с ModuleNotFoundError, затем прошёл.
  Замечания ревью о частичных страницах и замене медиа сначала воспроизведены
  отдельными падающими тестами, затем исправлены. Сейчас 7 контрактных тестов PASS.
- `./scripts/check python --files tests/collection/test_publication_adapter.py
  tests/collection/test_telegram_html_fixtures.py tests/collection/test_youtube.py
  tests/reader/test_material_list_data.py tests/reader/test_telegram_web.py
  tests/reader/test_telegram_preview_fragment.py tests/reader/test_telegram_download_partial.py
  tests/reader/test_external_telegram_media.py tests/reader/test_telegram_hidden_fallback.py
  tests/reader/test_telegram_http_settings.py --compact` — **87 passed**.
  Отчёт `.scratch/check-runs/20261022T205445Z-c381c672/report.json`.
- `./scripts/check build browser --browser-workers 2 --files
  tests/browser/test_telegram_download_partial_http.ts
  tests/browser/test_telegram_preview_fragment_http.ts
  tests/browser/test_telegram_v3_http.ts --compact` — **3 passed**,
  сборка и типизация браузера PASS. Проверены подключение, открытие, повторная
  загрузка, сохранённый фрагмент и обрыв файла. Desktop/mobile PNG просмотрены.
  Отчёт `.scratch/check-runs/20261022T205318Z-6236b04d/report.json`.
- `mypy oko --strict` — **91 модуль PASS**.
- Независимые Standards и Spec reviews: все замечания исправлены, повторное
  ревью затронутого кода на `741e0b1` без открытых замечаний.

## Полный прогон и ограничения доказательств

`./scripts/check all --compact` на снимке `3fb21dc`:
`.scratch/check-runs/20261022T204742Z-58830cfe/report.json`.
Контракты, сборка, syntax/lint, Python/браузерная/tooling типизация, HTTP-выдача,
entrypoint, diff и инструменты прошли. JUnit: 949 тестов, 25 падений;
браузер: 321 passed, 2 failed, 2 skipped.

Этот прогон выявил потерю прежней семантики фрагментов предпросмотра и принятых
частичных скачиваний. Она восстановлена. Все упавшие Python-сценарии включены в
успешный повтор 87 тестов; оба упавших браузерных сценария — в успешный повтор 3.
Успех повтора относится к перечисленным наборам, а не к новому полному прогону.
Незатронутые успешные статические/tool-наборы без необходимости не повторялись.

Ранние неуспешные запуски сохранены в check-runs: sandbox запретил `ps`; затем
выявлены строгое преобразование JSON и переклассификация недоступного поста.
Отдельный повтор Python стартовал до завершения сборки и упал из-за отсутствующих
assets; после завершения сборки тот же набор прошёл. Эти прогоны не считаются PASS.

## Границы шага

Адаптер принимает только внешние ссылки и поток файла. Временный преобразователь
сохраняет прежние внутренние ID, сопоставление альбомов, старые курсоры и выбор по
дате. Новые count-проходы используют общий API. Текст, цитаты, внешние вложения,
фрагменты предпросмотра и локальные файлы проходят прежнюю проекцию читателя.
Целевая схема Publication и независимая догрузка файлов относятся к #91/#92.

Push не выполнен: автоматическое approval review дважды отклонило его, сославшись
на ранний запрет реализации. Запрошено прямое подтверждение публикации кода,
создания PR и merge. PR не создан, issue #90 не закрыт, следующие задачи не начаты.
Рабочий экземпляр не обновлялся и не перезапускался; его SHA не проверен.
