# Telegram #21: итоговая предметная приёмка

Дата: 2026-10-16. База: `d51ed76660ec95e18b25ea3fd2d8d91914a5a1be`.
Актуальные [issue #21](https://github.com/tgenman/oko/issues/21) и comments (пусто) прочитаны перед работой.

## Объём и итог

Существующая интеграция #5 используется без второго API/формы. Проверена трассировка всех 12 сценариев §14 и зависимостей #17–20. Приёмка выявила и исправила потерю принятых результатов при служебной смене каталога версии, затем несовпадение текста/provenance при сбое повторной генерации. Обновлены текущие инструкции удалённого OPML; старые отчёты не переписаны.

Все данные временные, sources/HTTP/SDK/models синтетические. Настоящие Telegram-аккаунты, личные подписки, модели и Computer Use не использовались.

## 12 сквозных сценариев

| № | Требование и наблюдаемое доказательство |
| --- | --- |
| 1 | `test_web_connect_view_and_remove_never_create_a_worker_or_register_read_sync`: настоящий HTTP без конфигурации, worker construction и ReadSync eligibility запрещены guards; connect/сбор/view/delete проходят, telegram_reads пуст. `test_web_media_is_downloaded_locally_with_no_reader_credentials` проверяет файл и отсутствие auth/cookie. |
| 2 | `test_v3_telegram.py`: omitted/web/personal/collection, exact20, invalid422 без записи. `test_telegram_connection.ts` и настоящий `test_telegram_v3_http.ts`: web default, profiles GET/PUT, conflict, без account/auto_read у web. Контракт не содержит OPML/topic operations; существующие отрицательные HTTP-проверки сохраняются. |
| 3 | `test_web_binding_needs_no_account_and_duplicates_are_normalized`, `test_client_claims_are_global_and_access_is_immutable`, `test_concurrent_connect_has_one_winner_without_updating_folder`: URL/aliases/peer, один победитель, никаких upsert. Typed browser popup показывает legacy alias и фактическую роль всех владельцев. |
| 4 | `test_confirmed_alias_cannot_be_reassigned_to_another_peer`, `test_reassigned_public_username_cannot_replace_connected_peer`: подтверждённая смена peer отклонена; старый контент не подменён. Неизвестная web-смена username остаётся явно указанным пределом доказательства. |
| 5 | `test_profiles_require_verified_distinct_people_and_same_person_can_reauthorize`, `test_session_substitution_blocks_collection_and_same_person_recovers`, `test_used_profile_detects_external_replacement_and_recovers_same_person`; installed-SDK worker проверяет get_me до history/media/ack. |
| 6 | Усилен `test_blocked_personal_download_keeps_web_and_collection_live_until_stop`: зависший personal не блокирует web и collection, stop отменяет загрузку, файлы не публикуются. `test_subprocess_downloads_have_separate_account_pipes_and_spools` проверяет процессы. `test_flood_wait_survives_a_new_monotonic_origin_and_does_not_block_other_account` — durable wait без общей блокировки. Late media/stop — отдельные guards. |
| 7 | `test_telegram_history.py`, `test_web_catchup_keeps_old_frontier_and_resumes_remaining_pages_after_restart`, `test_album_first_part_deletion_and_new_url_keep_one_card_and_tombstone`, `test_worker_descending_snapshot_expands_boundary_album`: count/date, inclusive boundary, frozen manifest, альбом/перезапуск/catch-up/viewed. |
| 8 | `test_direct_articles_are_complete_local_and_unique_without_recursive_crawling` web/client, installed-SDK TextUrl, recorded album HTML, `test_html_quotes_poll_voice_sticker_and_unavailable_document_are_preserved`, iframe/no-network. Настоящий browser открывает Telegraph-текст, локальное фото и явную неполноту iframe. |
| 9 | Media/article completion не создают previous author version; changed known bytes/text создают. Новая проверка `test_telegraph_transient_failure_preserves_theses_but_real_text_edit_invalidates_them` в 4 вариантах filter/regenerating: transient сохраняет revision/results и не вызывает повторный AI; actual edit инвалидирует вход; поздний ответ старого запроса не заменяет новый. `test_retained_theses_and_provenance_come_from_the_same_committed_bytes`: fault after_theses_move и смена Codex Fast не меняют происхождение принятого старого результата. |
| 10 | `test_owned_worker_collects_all_topics_and_keeps_read_scopes_internal`, `test_whole_forum_worker_keeps_general_new_deleted_topics_and_refuses_broad_ack`, `test_forum_frontier_never_crosses_other_topic_and_partial_success_is_not_replayed`, restart partial ack. SDK fixture допускает только ожидаемые RPC: join/import-invite/toggle отсутствуют. Один binding и один поток. |
| 11 | Delete/crash/restart/claims/re-add в bindings, late discovery/media в bindings, late Telegraph в telegraph, cancel read_window actions=delete/stop/close в read_sync. До завершения connect новый feed ID не публикуется: `test_stopped_pending_connect_cannot_commit_a_late_binding` возвращает409 после stop и после рестарта подтверждает отсутствие фида. Удаление не создаёт viewed/read eligibility. Browser confirmation описывает локальную очистку и показывает новое добавление с новым ID. |
| 12 | `test_legacy_aliases_all_claim_owners_and_saved_material_survive_until_explicit_mapping`, topic preflight без изменения базы, legacy adoption и ordinary imported sources. `test_v3_connections.py` и browser RSS/YouTube из #5 сохраняют одиночное добавление, настройки и историю. OPML routes/parser/DTO сняты, tests/support setup использует одиночное добавление и локальные fixtures. |

## Фактические запуски и ограничения результата

- `.venv/bin/python scripts/api.py validate`: PASS, OpenAPI3.1, 40 routes.
- `npm run api:generate`: PASS; generated bytes не изменились. `npm run api:check`: PASS.
- Расширенный `check python types --files` по 18 Telegram/AI/recovery/lifecycle файлам: **147 passed, 2 failed**, contracts/types PASS, 21.18 s. Report `20261016T054357Z-c5c44587/report.json`. Два падения были счётчиком явной регенерации, а не потерей результатов; тест теперь различает исходные/новые запросы и ожидает started/finished Events. Этот запуск не объявляется полным PASS.
- После исправлений `test_telegram_telegraph.py`: **12 passed, 5.14 s**; отдельный types PASS72sourcefiles, report `20261016T054819Z-13b89e7d/report.json`.
- Финальный связанный `check python types browser --files` (Telegraph, coordination, theses recovery, YouTube freshness, v3 reading, Telegram connection/realHTTP browser): **32 Python passed, 6 browser passed**, contracts/types/tooling/browser-types PASS; **12.31 s**. Report `20261016T054858Z-94eb5ce9/report.json`. Это повтор затронутого набора, не всех149 сценариев и не all/tools.
- В исходном новом тесте сначала был неверный путь revision_id в DTO; исправлен на content.revision_id. Затем получен продуктовый RED: accepted theses=None после503 при прежнем revision. Исправление сохраняет только done derivatives при неизменном fingerprint и зарегистрированный retained результат во время регенерации.
- При усилении модели первая fixture возвращала многоабзацный point и дала4failed; приведена к допустимому одноабзацному результату. Детерминированные четыре варианта затем PASS2.31 s. Ограничение схемы не ослаблялось.
- Дополнительный RED по provenance: **1 failed,0.80s**, старый результат с fast=true вместо принятого fast=false. Исправлен единый выбор принятого файла для текста и происхождения; регрессия входит в итоговые12/32 PASS.
- Ручной `check staged`: PASS1.73s, report `20261016T055045Z-7d3b21cf/report.json`; whitespace чистый.
- Новых skips нет. Полный `./scripts/check all tools` остаётся обязательным на финальном результате шести задач (#7); промежуточная приёмка не является утверждением полного прогона или разрешением выпуска. Legacy baseline не восстанавливался: действующий api/README не требует approval/candidate gate.

## Ревью и визуальная проверка

Code-review выполнен двумя независимыми reviewers. Standards: исправлено несовпадение provenance; focused review0, независимая регрессия1PASS0.72s. Spec: исправлены недетерминированный счётчик/протокол регенерации и остаток OPML parser в README; focused review0, независимый повтор5PASS2.59s. Остальных actionable замечаний нет.

Свежие Playwright desktop/mobile снимки текущего прогона сохранены в `evidence/task21/`. Просмотрены формы профилей, web-подключения и подтверждение удаления; остальные снимки повторяют неизменённый #5 UI. Исторические картинки не переименованы в новый результат.

Синтетические SDK RPC подтверждают локальные границы и последовательность запросов, но не семантику реальных серверов Telegram. HTML fixtures закрепляют фактическую разметку своей версии, не универсальный контракт всех будущих страниц. Проверка подписей/допуска/поздних результатов не обещает отмену уже отправленного внешнего ack.
