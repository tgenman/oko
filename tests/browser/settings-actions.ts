import type {Page} from 'playwright';
const sections = {originals:'Чтение',theses:'Чтение',prompt_theses:'Промпты',prompt_filters:'Фильтрация',processor:'Обработчик'};
const titles = {originals:'Показывать исходную публикацию',theses:'Тезисы публикаций',prompt_theses:'Промпт тезисов',prompt_filters:'Правила фильтрации',processor:'Обработчик'};
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
  await page.getByRole('button',{name:/^Модели/}).click();
  await page.locator('[data-settings-target="' + (title === 'Codex' ? 'codex' : 'llamacpp') + '"]').click();
}
async function feedOptions(page: Page) {
  if (await page.locator('#feed-folder').isVisible()) return;
  await settingsHome(page);
  await page.getByRole('button',{name:'Подключение и параметры',exact:true}).click();
}
export {editSetting, settingsHome, modelPage, feedOptions};
