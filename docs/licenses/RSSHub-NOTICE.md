# RSSHub attribution

Telegram HTML selectors and extraction behavior in `oko/collection/telegram/web.py` and `telegraph.py`
are adapted to Python from DIYgod/RSSHub `lib/routes/telegram/channel.ts` and `lib/routes/telegram/telegraph.ts` at
commit `d24f5136487a388662ec6656e4f1ba4ff46b0ed8`.

Upstream: https://github.com/DIYgod/RSSHub/tree/d24f5136487a388662ec6656e4f1ba4ff46b0ed8
Copyright belongs to RSSHub's contributors. License: GNU Affero General Public
License version 3; the upstream license is preserved verbatim in
[RSSHub-AGPL-3.0.txt](RSSHub-AGPL-3.0.txt).

Oko adaptations use BeautifulSoup/html.parser and httpx, bounded saved history,
plain text, and local attachments. RSS generation, switches, teleproto, account
fallback, upstream caches and streaming routes are not included.
