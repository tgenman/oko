const {after} = require('node:test');
const {chromium} = require('playwright');

// node --test gives each file its own process. Only the browser is shared;
// callers own a fresh context and close it, including all of its pages.
let browser;
exports.newContext = async function() {
  browser ??= chromium.launch({headless: true,
    executablePath: process.env.OKO_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  return (await browser).newContext();
};

after(async () => {
  if (browser) await (await browser).close();
});
