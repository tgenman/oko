# Browser checks

`test_opened_material.cjs` runs the actual Oko HTML and JavaScript in headless Chrome. It exercises reader actions and observes DOM, playback and browser object URL lifecycle. Only external fetch responses are substituted. The delayed transport deliberately ignores AbortSignal so tests prove that late results are harmless independently of cancellation.

Install/use Node.js and Playwright (`playwright`, including its test assertions). Point `NODE_PATH` to the directory containing these packages when they are not locally installed. Set `OKO_BROWSER_EXECUTABLE` to Chrome/Chromium on other hosts; the default is the macOS Google Chrome installation.

```sh
NODE_PATH=/path/to/node_modules node --test tests/browser/test_opened_material.cjs
```

In Codex on this host, the bundled Node and packages are available via `load_workspace_dependencies`. The runner creates a temporary loopback HTTP server and a separate browser profile, closes both after each test, and uses no personal sources or AI. It needs permission to listen on loopback and launch Chrome when run in a restricted sandbox.

A generated canvas WebM exercises real video playback without external fixtures. These checks complement the Python reader/storage acceptance tests; they do not verify real RSS, Telegram, YouTube or model connections.
