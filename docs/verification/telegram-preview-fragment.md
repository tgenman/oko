# Фрагменты пересланных Telegram-постов

22 октября 2026. База 62436a77df1a2df145379130b2a8b2775469fe44.

## Проверяемое поведение

Пять публикаций с text_not_supported_wrap + forwarded_from сохраняют доступный
og:description с явной пометкой неполноты, ссылками на пост и оригинал пересылки.
Фрагмент не считается полной копией; его содержимое не интерпретируется как
авторский Markdown, отсутствующие URL/вложения не выдумываются.

Подтверждённая заглушка отделена от текста атрибуции. Fallback ограничен тем же
сообщением и подтверждает identity по widget script + Telegram site. Редиректы,
размер2MB и время30s ограничены; Runtime владеет HTTP admission/429/отменой.
По фрагменту не запускаются ИИ и Telegraph enrichment. Источник завершён без
ошибки и автоматических повторов; ручной повтор остаётся. Его неудача не стирает
старый fragment; настоящая полная копия имеет приоритет над metadata.

SourceManifest хранит preview_fragment/preview_error; HTTP-контракт не расширен.
Существующие linked_content + completeness legacy/message показывают fragment
и manual retry; wire post_web_unavailable остаётся для карточки целиком
недоступного поста. Подтверждение ограничения сохраняется в SourceManifest.

## Фактическая проверка

Новый HTTP сценарий первоначально воспроизвёл старую ошибку media-unsupported
вместо fragment (red). После правок локальный набор22 прошёл. Независимый reviewer
выявил потерю прежнего fragment при metadata failure; исправлено удержанием
непустого previous.preview_fragment и добавлен сценарий good→500→good.

Итоговый runner `.scratch/check-runs/20261022T114022Z-9411cbb2/report.json`:
- contracts/tooling-types/types90/browser-types/build PASS; generated API актуален;
- Python74 PASS: preview8, hidden fallback5, whole-post11, Telegraph18,
  network partial9, reload8, external media15;
- browser7 PASS: новый фрагмент и manual retry, hidden fallback, Telegraph,
  разрыв media stream, whole-post card, late response, previous version;
- desktop/mobile screenshots просмотрены, неполнота/ссылки/кнопка видимы,
  переполнения нет.

Новые HTTP сценарии также проверяют отказ от чужой identity/описания канала/
редиректа на другой пост, source complete без problems, no auto retry после
refresh/restart, no AI, миграцию legacy-error без author edit, genuine full-copy
приоритет и сохранение ожидания metadata429. Полный all не запускался.

Автоматические проверки используют временные данные и синтетические источники.
Рабочие пять постов проверяются отдельно после установки; результат фиксируется
в deployment receipt. Исследование и все URLs находятся в пользовательском
`docs/research/telegram-five-forward-only-posts-2026-10-22.md` исходного checkout.

После общего PASS уточнено буквальное отображение Markdown-подобного текста
metadata и полных URL. Первый дополнительный assertion на bare URL выявил
экранирование внутри URL (браузер FAIL 20261022T114233Z-26b33669). Исправлено
сохранением literal HTTP URL как autolink и экранированием только остальных
частей; label metadata не превращается в Markdown-link. Новый targeted browser
PASS 20261022T114431Z-0f302a6f, затем проверка точного plain текста без лишних
слешей и финальный mobile screenshot PASS 20261022T114508Z-b9f3e502. Screenshot
после ручного retry теперь ждёт фактического завершения работы через HTTP.
