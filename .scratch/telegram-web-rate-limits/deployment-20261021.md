# PR #66: слияние и обновление рабочего Око

2026-10-21. Выполнено по отдельному поручению пользователя.

- CI для head `055cf25d3926609448390a8b886f9b084fe609d1`: Checks #98,
  run `39587037636`, conclusion success. Build/static, Python, browser, tools
  и итоговый Full checks завершены success.
- PR #66 смёржен squash с expected head SHA. Merge commit:
  `e19467a7c118f2c58a8e1e2cb94a59a7163589dd`.
- Штатный `scripts/update-working-oko` получил origin/main и собрал linux/arm64
  образ в чистом временном clone, сохранив посторонние правки checkout.
- Предыдущий выпуск: `41817d02efd773ce497b61b73991ec0186a23ccf`.
- Согласованная резервная копия:
  `/Users/dabondarev/Library/Application Support/Oko/backups/20261021T111741346189Z`.
- Первый update остановился при запуске нового выпуска: проверка bind порта 8765
  сообщила занятость после остановки старого контейнера. Проверка lsof и docker ps
  не нашла слушателя или работающих контейнеров. Посторонние процессы не завершались.
  Повторный `scripts/oko-release start` успешно запустил уже выбранный новый выпуск.
- Итоговый release status: единственный контейнер running/healthy, его image совпадает
  с manifest `sha256:4978c18c44bba416a36982f07880784e15889c4e1874df801b85c2aacd6c50b1`;
  helper_loaded=true. Штатный start подтвердил authenticated health helper из контейнера,
  protocol 1 и соответствие release; inference не выполнялся.
- HTTP `/api/v3/status`: 200, runtime_state=running.
- HTTP `/api/v3/settings/telegram-http`: 200, value=2.0.
  Read-only probes не создавали браузерных credentials и не меняли настройки.
- `git rev-parse origin/main` и `git ls-remote origin refs/heads/main`:
  `e19467a7c118f2c58a8e1e2cb94a59a7163589dd`.
- Запущенный выпуск:
  `e19467a7c118f2c58a8e1e2cb94a59a7163589dd`.
- Пользовательские изменения package.json и прототипов сохранены. Текущий checkout
  не переключался. Этот локальный отчёт не изменяет main или установленный выпуск.

Логи команд: `/tmp/oko-pr66-deploy.log`, `/tmp/oko-pr66-start.log`.
