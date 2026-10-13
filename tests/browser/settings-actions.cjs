const sections = {events:'Объединение событий',event_hours:'Объединение событий',originals:'Чтение',theses:'Чтение',digests:'Обзоры за период',schedule:'Обзоры за период',digest_days:'Обзоры за период',prompt_theses:'Промпты',prompt_digest:'Промпты',prompt_filters:'Фильтрация',processor:'Обработчик'};
const titles = {events:'Объединять события',event_hours:'Окно события, часов',originals:'Показывать исходную публикацию',theses:'Тезисы публикаций',digests:'Обзоры за период',schedule:'Расписание обзора',digest_days:'Период обзора, дней',prompt_theses:'Промпт тезисов',prompt_digest:'Промпт обзора',prompt_filters:'Правила фильтрации',processor:'Обработчик'};
async function settingsHome(page) {
  const dialog = page.locator('dialog[open]');
  while (await dialog.locator('.settings-back').isVisible()) await dialog.locator('.settings-back').click();
}
async function editSetting(page, key) {
  if (await page.locator('#setting-' + key).isVisible()) return;
  await settingsHome(page);
  await page.getByRole('button',{name:sections[key],exact:true}).click();
  await page.getByRole('button',{name:titles[key],exact:true}).click();
}
async function modelPage(page, title = 'llama.cpp') {
  await settingsHome(page);
  await page.getByRole('button',{name:/^Модели/}).click();
  await page.locator('[data-settings-target="' + (title === 'Codex' ? 'codex' : 'llamacpp') + '"]').click();
}
async function feedOptions(page) {
  if (await page.locator('#feed-folder').isVisible()) return;
  await settingsHome(page);
  await page.getByRole('button',{name:'Подключение и параметры',exact:true}).click();
}
module.exports = {editSetting, settingsHome, modelPage, feedOptions};
