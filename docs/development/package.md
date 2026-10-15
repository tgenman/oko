# Поставка читателя и контракта

Из checkout с установленными закреплёнными Python-группы `dev` и npm-зависимостями:

```sh
npm run build
poetry run python scripts/build-package.py --output /tmp/oko-wheels
```

Используются Poetry 2.5.1 и backend poetry-core 2.5.0. Команда выпуска — `scripts/build-package.py`: прямой `poetry build` не выполняет её проверки и подготовку временного дерева.

Сборка проверяет derived Python/TypeScript библиотеки, свежесть исходников, canonical digest и целостность frontend assets. Wheel содержит только текущую законченную frontend generation, встроенный Swagger и generated backend DTO/контракт. Подготовка выполняется во временном дереве: `build/` и egg-info в checkout не создаются. Wheel устанавливается обычным Python package installer; Node и исходники нужны для подготовки, установленному приложению они не нужны.

В работающем Око `/docs` показывает Swagger, `/openapi.json` — canonical contract. Кнопка Authorize принимает token локальной сессии; после неё Try it out → Execute отправляет запрос. Авторизация находится в памяти страницы и пропадает после загрузки нового документа. Swagger не получает token автоматически и не создаёт reader lease. Поэтому прежняя остановка Око без живой вкладки читателя действует и при открытой документации.

Когда читатель остановлен, используйте самостоятельный просмотр:

```sh
npm run api:docs
```

Его адрес — `http://127.0.0.1:8766/docs`; там нет API-вызовов и авторизации. Оба варианта используют локальные assets и не ослабляют CSP.

Smoke поставки устанавливает настоящий wheel через pinned installer во временный каталог, выполняет HTTP и Playwright из другого cwd с пустым PATH и проверяет, что Python загружает Око из установленного wheel. В тестовый каталог копируется только общая синтетическая фабрика; production исходники checkout не используются. Команды проверки:

```sh
poetry run python -m pytest tests/api/test_installed_package.py tests/reader/test_frontend_build.py -q
node --test tests/browser/test_installed_docs.cjs
```
