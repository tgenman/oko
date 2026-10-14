# 04. Generated SDK, transport и сессия читателя

Status: ready-for-agent
Blocked by: 03

Спецификация: [spec.md](../spec.md). Реализация пока не разрешена.

## Результат

Production-фронтенд вызывает generated SDK; его transport сохраняет правила Око, а вкладка имеет одного владельца heartbeat, polling и межвкладочных сообщений.

## Объём

- Подключить SDK этапа 02 вместо вручную составляемых JSON-вызовов. Сохранить один временный адаптер для legacy callers; он не создаёт новый список wire-схем.
- Перенести настройку transport, описание ошибок и ui-operations на strict TS. Сохранить диагностические/action headers, confirmed/expected error, media, signal, keepalive и авторизацию только своего origin.
- Выделить владельца сессии из app.js: tab ID, heartbeat, refresh admission, BroadcastChannel, pagehide/pageshow и прекращение работы. Не менять интервалы/политику повторов и семантику остановки.
- Использовать контракт всех операций из OpenAPI; недостающая функция SDK или расхождение ответа исправляется в canonical source/codegen по результатам HTTP-проверки, а не локальным any/assertion.

## Приёмка

- [ ] Смена области, просмотр и остановка отбрасывают старые ответы; независимые операции не отменяют друг друга.
- [ ] Реальная HTTP-ошибка, ошибка сети и не-JSON ответ сохраняют прежнее различение; токен не уходит на чужой origin, binary не декодируется как JSON.
- [ ] Повторный setup/cleanup не создаёт второй цикл polling/heartbeat и не отправляет stop/close из-за React-размонтирования.
- [ ] Протокол последней вкладки, keepalive, межвкладочный просмотр и восстановление страницы сохранены; transport не повторяет мутации автоматически.
- [ ] Production imports ведут к generated SDK; запросы нового API не описываются вручную параллельно с OpenAPI, нет обхода типов через any/assertions.

## Проверка

`test_transport.cjs`, `test_contracts.cjs`, `test_reader_http.cjs`, затронутые проверки Runtime/вкладок через HTTP. На интеграции первого React-участка повторно проверить подписки в StrictMode. Моки не подменяют владельца актуальности.
