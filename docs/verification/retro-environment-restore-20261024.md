# Восстановление четырёх улучшений #44

Дата: 2026-10-24. База: `44e11b4ab858e22ab02e34e962dfa9589ad86f12`.
Ветка `codex/restore-retro-44`, отдельный managed worktree.
Задание: восстановить все четыре улучшения из [PR #44](https://github.com/tgenman/oko/pull/44)
на актуальном main с проверками; закрыть диагностический #85; рабочее Око не трогать.
Исходный checkout и его untracked `.scratch/password-login/` сохранены.

## Результат

1. `Clock.options()` возвращает clock/wall_clock вместе. HTTP-проверка сохраняет
   сессию при скачке системного времени и завершает её по управляемым часам.
2. Pytest plugin и TAP parser дают `FAIL NOW`, причину, полный worker log и
   точечный Retry до завершения соседних групп. Ошибка объявляется один раз;
   остальные тесты продолжаются, exit codes и cleanup прежние. Сохранённый
   check-report читает failure_details вместе с нынешними counts и timings.
3. `prepare-worktree` переиспользует установленные .venv/node_modules, строит
   frontend и последовательно вызывает validate_frontend. Ошибки сборки/валидации
   останавливают подготовку. Уже существующие пути сохраняются; личные данные
   и .env не копируются, пакеты не устанавливаются.
4. `github_issues.py search-call` генерирует вызов действующего MCP-плагина:
   компактный number/title/state/url, полный ответ остаётся в store. Сеть и
   авторизация принадлежат плагину. Неизвестный state остаётся null.

Интеграция сохраняет JUnit, browser timing reporter, медианы времени, полный
inventory, регистрацию tools, caps и матрицу CI. Plugin добавляется после
инструментации JUnit; минимальные копии runner без plugin сохраняют стандартный
pytest. Node Retry выбирает node, HTTP — python. Snapshot перенаправляет Retry
на исходный checkout только при совпадении собственного ROOT.
Исторический отчёт #44 не переписывается. PR #85 закрыт без слияния по запросу;
его диагностический код в main не добавлялся.

## Проверки до полного набора

- Первое применение старого diff остановилось конфликтом добавленных тестов
  check-report: пробный pytest получил ошибку collection на conflict markers.
  Конфликт разрешён сохранением обеих групп, это не считается red-проверкой поведения.
- Корректный red: 4 failed за11.14с — нет раннего FAIL NOW, failure_details,
  search-call и prepare-worktree.
- После переноса: 41 passed за12.33с — feedback/report/search/preparation и HTTP Clock.
- Настоящий prepare-worktree собрал и провалидировал assets этого worktree.
- Runner/timings/snapshot/build/lifecycle/YouTube: 100 passed за46.93с.
- Независимое ревью нашло неверный browser Retry для Node. Новая проверка реального
  Node TAP воспроизвела ошибку (1 failed за0.65с); исправлена группа node.
- После исправления: 43 passed за9.09с. Включены 3 Node TAP случая: обычный запуск,
  свой snapshot и унаследованный snapshot другого checkout; counts и filename сохранены.
- Генерируемый MCP-код выполнен через functions.exec: success, 2 компактные записи,
  тела issues не печатались, полный ответ сохранён, state=null не подменён.
- Повторное read-only ревью Node/snapshot/plugin — без замечаний. Reviewer тесты не запускал.

Все данные и внешние ответы тестов синтетические, хранилища временные.
Рабочая установка, пользовательские подписки, Telegram и настоящий AI не используются.
Коммит, push и слияние в объём этого задания не входят.


## Первый полный прогон и исправления фикстур

`./scripts/check all tools --browser-workers 8 --compact`, frozen tree
`95db8a6b1929906c8e220764857c1aa8e5aab415`: FAIL за192.33с.
910 Python passed/1 failed,246 tools passed,113 Node passed,
220 browser passed/1 failed/2 прежних skipped; performance batch не запускался.
Static/types/contracts/build/HTTP/entrypoint/diff и checkout guard PASS.
Отчёт: `/private/tmp/oko-restore-44-20261024/20261024T175406Z-e4078a0b/report.json`.

Оба отказа оказались гонками существующих тестовых фикстур:

- Telegram 429: счётчик снимался после POST collect, хотя воркер мог выполнить
  ожидаемый запрос до ответа POST. Замер перенесён перед POST; добавлен status200.
  Один запрос, запрет следующих при 429, RSS и сохранение паузы после рестарта
  проверяются прежними assertions. Весь файл:8 passed за10.46с.
- Folder delete: gate из предыдущего main перехватывал старый reading-composition,
  UI использует POST reading/check. Gate теперь устанавливается до навигации;
  после открытия empty dialog удерживает ответы реального poll, включая in-flight.
  DELETE действительно выполняется и обязан дать409; blocked heading и сохранность
  папки проверяются. Promise.all связывает клик и ожидание без unhandled rejection.
  Весь файл:4 Playwright passed за12.98с, tooling/browser types PASS.
  Снимок folder-menu-nonempty.png получен Playwright и просмотрен: виден запрет
  удаления непустой папки. Фактический UI и продуктовый код не изменялись.

Read-only ревью обеих фикстур: без замечаний и ослабления проверок.
На реальном отчёте отказа новый check-report сохранил обе failure_details,
worker timings и существующие counts; каждый FAIL NOW объявлен ровно один раз,
команды Retry указывают на исходный worktree, полные worker logs существуют.
Успех целевых повторов не подменяет результат первого полного прогона.

## Второй полный прогон

Frozen tree `6a40d06fd177a2cb91372130d19adb6fef0ace79`: FAIL за189.07с.
Python911 passed, tools246 passed, Node113 passed, browser220 passed/1 failed/2 skipped;
performance batch не запускался. Обе предыдущие фикстуры прошли.
Отчёт: `/private/tmp/oko-restore-44-20261024/20261024T175829Z-a93e9a13/report.json`.

Единственный отказ — существующий linked-publications RSS сценарий: Retry стал
недоступен из-за pending новой версии. Снимок перед setArticleState(ready)
просмотрен: Retry enabled, pending отсутствует. Между переключением источника
в ready и кликом автоматический retry (интервал1с) мог успеть загрузить статью;
новая версия намеренно блокирует действия старой открытой версии.
Неизменённый отдельный файл:2 passed за13.80с; это не подменяет полный FAIL.

Фикстура теперь переключает статью в ready в одноразовом route реального
POST material/retry, после клика и до передачи запроса backend. Настоящий retry,
новая версия, тезисы, ссылки на источники, media retry и изображение проверяются
прежними assertions. UI и производственное поведение не меняются.
Повтор файла:2 passed за13.73с, tooling/browser types PASS.
Независимое read-only ревью: без замечаний; одноразовый route не перехватывает
последующий retry медиа. После этого запущен третий полный frozen-набор.


## Итоговый полный результат

Третий frozen `./scripts/check all tools --browser-workers 8 --compact`:
**PASS,189.79с**. Python/HTTP911 passed, tools246 passed, Node113 passed,
Playwright225 passed/2 прежних skipped. Contracts, tooling/browser types,
syntax/lint, frontend types/build, HTTP, diff и entrypoint PASS.
Checkout guard PASS, changed_files пуст; timing history обновлена после успеха.

Проверенный tree: `008c412d5bd2bc708568e55a217431d86a8db693`.
Отчёт: `/private/tmp/oko-restore-44-20261024/20261024T180313Z-d1c4e6d8/report.json`.
После запуска менялся только этот фактический отчёт; исполняемый код сверён
с проверенным tree. Два предыдущих полных FAIL и адресные повторы сохранены выше.
База origin/main повторно сверена:44e11b4a. PR #85 перечитан через GitHub:
state=closed, merged=false. Рабочее Око не проверялось и не изменялось.
Изменения подготовлены в индексе отдельной ветки, без коммита и публикации.
