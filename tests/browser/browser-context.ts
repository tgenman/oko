import type {Browser} from 'playwright';
import {after} from 'node:test';
import {chromium} from 'playwright';

// node --test gives each file its own process. Only the browser is shared;
// callers own a fresh context and close it, including all of its pages.
let browser: Promise<Browser> | undefined;
export async function newContext() {
  browser ??= chromium.launch({headless: true,
    executablePath: process.env.OKO_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await (await browser).newContext();
  context.setDefaultTimeout(5000);
  context.setDefaultNavigationTimeout(10000);
  return context;
};

after(async () => {
  if (browser) await (await browser).close();
});
