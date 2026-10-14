# Перенос чтения на React и generated SDK

2026-10-14. Ветка codex/oko-react-reading, исходная база 830f7fb. Объём: тикеты 16–19. Личные источники, аккаунты и пользовательский экземпляр не использовались.

16–18: сохранены результаты в соответствующих тикетах. Tree — 6 browser + 4 iOS, list — 11 reading/HTTP browser + 2 appearance, opened material — 19 opened/list browser + 12 opened/surfaces/appearance после уточнения JSX/cleanup; HTTP, strict TypeScript/build/lint прошли. Desktop/mobile снимки просмотрены исполнителем. Эти результаты относятся к соответствующим коммитам f8f6c23, 77c8ec7 и d497f5a.

19: возобновлена незавершённая работа; dirty состояние сохранено в /tmp/oko-reading-resume-snapshot. Исправлен обнаруженный strict TS дефект параметра generated createDigest: scope/owner вместо folder. React owns digest/completion draft, request generation and native dialog lifecycle. Success reconciles reading even after close, while a stale response cannot close a new dialog. Completion conflict keeps open material and uses the refreshed server token.

Фактическая проверка:

- strict TypeScript и production build с api drift checks: PASS.
- Первый runner был заблокирован sandbox process inventory; браузер не запускался. После разрешённого loopback/process access: late digest PASS.
- Первый расширенный browser прогон: 1/3 PASS, две новые проверки ошибочно использовали отсутствующий selector #article. Исправлен только selector на существующий #text, повтор: 3/3 PASS, report 20261014T134424Z-e006878c.
- lint, 11 HTTP, 4 browser (folder commands + production HTTP reader), 11 Python folder-completion tests: PASS, report 20261014T134525Z-809ccf43.
- После добавления проверки timestamp dates и period/compress mode: folder browser 4/4 PASS, report 20261014T134641Z-766c0d2a. Неизменные HTTP/Python наборы повторно не запускались.
- Снимок folder-completion-conflict.png из report 20261014T134424Z-e006878c просмотрен: текст подтверждения, количество и сообщение конфликта видимы, оформление сохранено.

Полная интеграционная проверка всех owners и независимое ревью выполняются основной задачей миграции.
