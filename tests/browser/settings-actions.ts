import type {Page} from 'playwright';
const sections = {theses:'Reading',prompt_theses:'Prompts',prompt_filters:'Filtering',processor:'Processor'};
const titles = {theses:'Publication Summaries',prompt_theses:'Summary Prompt',prompt_filters:'Filtering Rules',processor:'Processor'};
async function settingsHome(page: Page) {
  const dialog = page.locator('dialog[open]');
  while (await dialog.locator('.settings-back').isVisible()) await dialog.locator('.settings-back').click();
}
async function editSetting(page: Page, key: keyof typeof sections) {
  if (await page.locator('#setting-' + key).isVisible()) return;
  await settingsHome(page);
  await page.getByRole('button',{name:sections[key],exact:true}).click();
  await page.getByRole('button',{name:titles[key],exact:true}).click();
}
async function modelPage(page: Page, title = 'llama.cpp') {
  await settingsHome(page);
  await page.getByRole('button',{name:/^Models/}).click();
  await page.locator('[data-settings-target="' + (title === 'Codex' ? 'codex' : 'llamacpp') + '"]').click();
}
async function feedOptions(page: Page) {
  if (await page.locator('[data-settings-page=feed-connection]').isVisible()) return;
  await settingsHome(page);
  await page.getByRole('button',{name:'Connection and Options',exact:true}).click();
}
export {editSetting, settingsHome, modelPage, feedOptions};
