import type {Browser} from 'playwright';
import {after} from 'node:test';
import {chromium} from 'playwright';

// Each file connects independently to the suite browser (or launches its own).
// Closing a connected Browser disconnects only that file;
// callers own a fresh context and close it, including all of its pages.
let browser: Promise<Browser> | undefined;
export async function newContext() {
  browser ??= process.env.OKO_TEST_BROWSER_WS ? chromium.connect(process.env.OKO_TEST_BROWSER_WS) : chromium.launch({headless: true,args:['--host-resolver-rules=MAP oko 127.0.0.1','--no-proxy-server'],
    executablePath: process.env.OKO_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await (await browser).newContext();
  context.setDefaultTimeout(5000);
  context.setDefaultNavigationTimeout(10000);
  return context;
};

after(async () => {
  if (browser) await (await browser).close();
});
