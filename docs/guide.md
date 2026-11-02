# User guide

Oko collects publications into a local library and presents them as topic folders, cards, and opened material. The current interface is in Russian; this guide includes key labels so you can find the corresponding controls.

For installation commands, start with the [README](../README.md#quick-start).

## Start with a separate library

```sh
# Synthetic publications for exploration.
uv run --locked python -m oko --database-url-file /path/to/private-demo-dsn --demo --data /tmp/oko-demo

# Your own sources and persistent local data.
uv run --locked python -m oko --database-url-file /path/to/private-reader-dsn --data ./data
```

The default data directory is `data/` relative to the working directory. Pass an explicit path when you want a stable location across launches. Repeating the launch command with the same directory opens the existing compatible instance.

The default server uses an available local port and opens the browser automatically. `--port 8765` selects a fixed port; `--no-browser` suppresses automatic opening. The default instance follows reader-tab presence: heartbeats run every two seconds, the default lease is six seconds, and the reconnection grace period is fifteen seconds. `--lease` and `--grace` accept positive values.

Use `--persistent` for a browser reader that stays available without open tabs, or `--api-only` for an explicit-lifetime API server without the reader UI. These are separate profiles. Stop an existing instance before changing its lifetime profile.

## Add and organize sources

Open **Add feed** (`Добавить фид`). For RSS or YouTube, paste the RSS/Atom URL and check the feed before saving. A YouTube channel Atom feed is recognized as YouTube. Checking fills in the title; you can edit it and select or create a topic folder. Re-adding a source opens the existing feed rather than resetting its history.

Each feed belongs to one topic folder. Feed importance is separate from folder order. Oko, folder, and feed settings inherit field by field; **Reset** (`Сбросить`) restores inheritance for a field. Initial history, public AI permission, importance, and Telegram-specific settings belong to the feed itself.

Open **Feeds and folders** (`Фиды и папки`) to edit a source, inspect effective settings, and follow history progress. General processing and model connections are under **Oko settings** (`Настройки Око`). Unsaved form drafts remain local to the open browser tab; closing a menu does not save them.

### Initial history and updates

When connecting a source, choose the latest N publications (default 20). Initial history has saved progress and can be paused without stopping new arrivals.

History capabilities depend on the source. RSS/Atom collection is limited by the feed's available history. YouTube can also select the latest N regular videos and archived streams through yt-dlp, excluding Shorts and without downloading video/audio. Its feed settings provide an explicit history-loading action. Known publications count toward the selected window, and explicit history collection can rediscover a publication deleted after viewing.

An update collects new publications, continues eligible incomplete downloads, and retries failed processing subject to source delays. It preserves successful current processing results. Manual actions do not bypass provider cooldowns.

### RSS and article extraction

Oko saves the announcement, attempts to extract the linked article without executing JavaScript, and saves supported images locally. Dynamic pages, paywalls, and access restrictions can leave an announcement with a visible incompleteness reason. A missing entry in a later RSS response does not prove the source deleted it.

### Telegram

Connect a public Telegram channel by its username or public link. Oko collects its publicly visible web pages without a Telegram account. Private channels, groups, supergroups and forums are not supported.

Albums appear as one publication. Supported images, video, audio, and files are saved locally; unsupported or unavailable attachments leave an explicit reason and retry action. Oko does not transcribe them. Direct Telegraph article links can supply article text and supported media; arbitrary links inside articles are not recursively followed.

Ordinary updates collect new posts and continue incomplete downloads. Use **Reload** (`Загрузить заново`) for an explicit refresh of a complete saved post. Reload accepts a fresh description and then refreshes media separately; an attachment failure remains visible.

Oko never marks messages read in Telegram. Marking a publication viewed in Oko removes its local reading content; removing a feed does not affect Telegram.

See [public access and recovery](verification/telegram-public-recovery.md) for bounded live checks.

### YouTube

Oko collects video metadata, a local preview, and available captions when required by summary settings or high importance. It does not download full video/audio or perform speech recognition. Missing captions and access failures are shown explicitly; successfully saved captions are reused.

Caption settings offer direct access or a dedicated HTTP/HTTPS proxy. Proxy credentials are entered separately from its address. Blank credential fields preserve existing values; removal is explicit. The proxy applies only to caption retrieval, with no automatic direct fallback. Saving settings does not test YouTube.

Caption starts share an Oko-wide interval and provider blocks cause a shared persisted cooldown. Repeated blocks increase the delay; manual retry and proxy changes do not bypass it. These are Oko's policies, not a promise of upstream availability. See [YouTube verification](verification/youtube-updates.md).

## Read and finish material

Opening a card or copying text preserves it. Collected originals and optional generated summaries can be available together; you can read saved material before AI processing finishes.

**Viewed** (`Просмотрено`) is an explicit completion action. It deletes collected content, media and derived results in Oko. It is not an archive action. There are no permanent viewed tombstones in the current pipeline: rediscovery or an explicit history reload can bring a deleted publication back.

Filtering can hide material without marking it viewed. Hidden material remains available with its reason. Completing a folder includes its hidden and not-yet-loaded publications, according to the server's confirmed composition.

Oko stores only the latest publication state. Already opened content may remain temporarily in the browser until you switch to the update. Source unavailability or deletion does not remove an already saved unviewed copy. Late downloads or model responses cannot restore content after viewing.

## Optional AI processing

Connect a processor in **Oko settings → Model connections** (`Настройки Око → Подключения моделей`), then select it and enable filtering or publication summaries at the desired settings level. Opening a card does not itself trigger generation. Effective settings and permissions determine eligibility.

Public AI processing requires explicit permission for the feed. Disallowed work waits for a settings change rather than silently switching processors. Review permission for each source before enabling it.

### llama.cpp

Connect an existing llama.cpp server and select its loaded model. A connection check does not send publication content. For feeds without public AI permission, local processing requires explicit confirmation that the model runs on this computer without remote RPC, plus loopback and model metadata checks by Oko. These checks cannot attest an arbitrary server.

The adapter sends saved text and the effective prompt, validates the response schema, and publishes only a complete accepted response. Oversized input produces an explicit error rather than silent truncation. Oko cancels its own HTTP request when work becomes obsolete; it does not stop your model server.

You manage server-side logging and caches. Oko's local checks do not promise physical erasure of server memory. See [llama.cpp verification](verification/llamacpp-theses.md) for constraints and the scope of real-model checks.

### Codex

The optional Codex integration uses an existing supported ChatGPT sign-in and a specifically admitted CLI binary. On macOS, Oko prefers the CLI bundled with ChatGPT; `OKO_CODEX_BINARY` can select an explicit path. Admission is tied to the checked binary's SHA-256, so an updated or different build needs verification before use.

Connection diagnostics and fresh quota checks gate execution. Unknown or exhausted quota blocks work. Oko does not reserve quota or promise that a subscription covers every workload. Model, reasoning effort, and Fast selections apply to future work; completed results retain their provenance and are not automatically regenerated by those changes.

In the managed Docker installation, a separate host process runs Codex. See [Codex verification](verification/codex-theses.md), [CLI upgrade checks](verification/codex-cli-upgrade.md), and the [Docker runbook](development/docker.md).

## Data, diagnostics, and backup

Your data directory contains media files and integration settings; publication content, results and durable state live in PostgreSQL. Keep it separate from disposable demo directories. Marking material viewed removes its local reading content; it does not erase historical copies in backups or upstream services.

The **Diagnostics** (`Диагностика`) panel shows browser, API, collection, and processing failures. Repeated errors are grouped. Recent diagnostic groups persist in `debug.json` under the data directory. Diagnostics can include source context and tracebacks; inspect them before sharing. Credentials and session files should remain private.

For the managed macOS installation, data lives under `~/Library/Application Support/Oko/data/`, outside the checkout. Use the [release runbook](development/docker.md) for installation, consistent backups, updates, rollback, and full shutdown. Its launcher opens the installed release rather than running the working checkout.

## Further reading

- [Architecture overview](architecture/README.md): ownership, data flow, cancellation, and persistence.
- [API guide](../api/README.md): generated clients and the canonical HTTP contract. A running instance serves `/docs` and `/openapi.json`; Swagger requires explicit authorization and does not keep the reader alive.
- [Verification reports](verification/): actual checks and their limits.
- [Previous Russian README](README.previous.ru.md): archived detailed notes, including older behavior and numeric limits. Treat version-specific claims as historical.
