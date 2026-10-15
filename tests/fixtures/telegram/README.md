# Публичная HTML-разметка Telegram

Получено 2026-10-15 через curl с `Mozilla/5.0`, без cookies, аккаунта,
клиента Telegram и скачивания самих медиа. Полные ответы сохранены без
редактирования; SHA-256 и исходные URL находятся в manifest.json.
Нельзя открывать snapshots как доверенный HTML приложения.

- public.html — https://t.me/s/telegram?before=410 : текст, ссылки,
  фото через CSS background-image и видео с настоящим `video src`.
- album.html — https://t.me/s/durov?before=400 : wrapper `durov/373`,
  девять anchors `373?single`–`381?single`, явная пагинация `before=373`.
  У grouped video есть preview и настоящий `video src`, а также fallback
  `message_media_not_supported`. Fallback сам по себе не доказывает отсутствие
  файла: нужно отличать его от supported-разметки и ошибок скачивания.
- empty.html — https://t.me/s/telegram?before=1 : валидная история канала
  без message wrappers и без ссылки продолжения.
- unavailable.html — https://t.me/s/oko_fixture_missing_20261015 : landing
  page Telegram вместо истории; отсутствие posts не означает конец истории.

Manifest — oracle структуры, вручную сверенный по исходной разметке.
Тест fixtures проверяет её HTMLParser из stdlib; production parser будет
реализован в #20 через BeautifulSoup. Эти файлы не доказывают, что любой
публичный канал имеет тот же markup, что CDN URL ещё доступны, что доступны
все медиа, или что у веб-источника известен устойчивый numeric peer ID.
Публичные signed CDN URL — часть ответа, не credentials Око; тесты их
никогда не запрашивают. Исходное содержание принадлежит авторам каналов.

Код RSSHub здесь не переносился. При переносе в #20 сохранить upstream
commit/путь и AGPL-3.0 согласно спецификации; fixtures не меняют эту обязанность.

`unavailable-media.json` — отдельный **синтетический** ответ HTTP 403 для
реального video src внутри сохранённого album member. Это подготовленный
network fixture для #20: пост и остальные вложения остаются, материал
неполон, повторяется только недостающее вложение. Он не выдаётся за
измеренный отказ CDN и не заменяет raw HTML. Не делать запросов по fixture URL.
