# Частичная загрузка при обрыве Telegram web
Пользователь: сохранять частичный результат, красный блок со ссылкой и причиной, прекратить автоповторы, оставить ручной повтор, main + обновить рабочее Око. Сохранить Telegraph, скрытые заглушки, пять standalone-постов без текста не изменять.

Граница: network/media-unavailable в WebTelegram.fetch с пригодным текстом/вложениями принимается как download_partial в SourceManifest. Поток ProtocolError превращается в SourceError; оборванные байты удаляются, полученные ранее файлы сохраняются. Не отменять SourceDeferred/429 и терминальные mixed ошибки. Обычный сбор и перезапуск не повторяют принятую частичную загрузку; explicit retry повторяет. Причина проецируется в linked_content красным Markdown callout со стабильным Telegram permalink конкретного member, signed CDN URL не публикуется. Флаг не создаёт ложных авторских правок.

Проверки: HTTP temp Store + синтетический оборванный stream/HTTP500, retained/no retained, manual recovery; регрессии reload/Telegraph/external/hidden fallback; браузер red block + manual retry. Полный прогон не требуется.
