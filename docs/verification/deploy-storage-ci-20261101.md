# Storage release preparation — 2026-11-01

Main 7e065f11 could not publish: Checks 41215582697 failed browser-1 and timed
out python-2; other groups, including PostgreSQL integration, passed.

This follow-up changes tests only:

- Browser storage-reset test now observes accepted reset before releasing an old
  response. Three targeted repeats passed; removing the epoch/storage guard in
  an isolated negative control caused the assertion to fail as intended.
- Both HeldModel tests release the cancellation-resistant synthetic model in
  finally. This prevents a failed assertion from hanging teardown and masking
  the original failure. The historical CI hang location is inferred from test
  order; no hung-process stack was available.
- Removed one post-start configuration test with injected SQL sleeps. The user
  deferred performance optimization and authorized removing unnecessary tests.
  Retained recovery tests cover readable cards, settings/viewed, restart and
  delayed work. The exact artificial-latency combination loses coverage.
- Telegram spacing tests retain the minimum interval and functional assertions;
  scheduler-dependent upper elapsed limits are removed.

The retained targeted PostgreSQL selection passed six tests; test-contract and
whitespace checks passed. This is not a full CI pass or deployment confirmation.

Before deployment, the live reader and release manifest both reported 24a56072.
Reader/PostgreSQL containers were healthy. Playwright login, status/navigation/
cards HTTP 200 and opening a material passed without JavaScript errors; navigation
and cards each reported 1507 entries. Screenshot was inspected locally. No viewed,
subscription or configuration command was issued. Following separate explicit
user approval, TEMPORARY on database oko was granted to oko_reader and read back
as true. No schema migration has been run at this preparation stage.
