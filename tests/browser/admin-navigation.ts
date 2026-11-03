import type {Page} from 'playwright';
export async function openAdmin(page:Page){
 if(new URL(page.url()).pathname==='/admin')return;
 await page.locator('#app-menu summary').click();
 await page.locator('#admin-link').click();
}
export async function openReading(page:Page){
 if(new URL(page.url()).pathname==='/admin')await page.locator('#reader-link').click();
}
