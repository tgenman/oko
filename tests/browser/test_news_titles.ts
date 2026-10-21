import {test} from 'node:test';
import {reader,material,expect} from './support';
import {artifactPath} from './artifacts';

test('long news heading keeps its full text and list omits post excerpt', async()=>{
 const r=await reader();try{
  const label='Bloomberg',url='https://example.test/news/'+('long-path-'.repeat(25));
  const headline='В результате прошлогодней кибератаки на медицинское подразделение Oracle были раскрыты данные почти 20 млн человек. '+('Дополнительный текст заголовка. '.repeat(5));
  const title=`[${label}](${url}): ${headline.trim()}`,expected=`${label}: ${headline.trim()}`;
  await r.start([material('news',{title,snippet:title,text:title+'\n\nСледующий абзац публикации.'})]);
  const row=r.page.locator('[data-card="news"]');
  await expect(row.locator('.card-title')).toHaveText(expected);
  await expect(row.locator('.card-title')).toHaveAttribute('title',expected);
  await expect(row.locator('.card-snippet')).toHaveCount(0);
  await row.click();
  await expect(r.page.locator('#title')).toHaveText(expected);
  await expect(r.page.locator('#text')).toHaveText(expected+'\n\nСледующий абзац публикации.');
  await expect(r.page.locator('#text').getByRole('link',{name:label,exact:true})).toHaveAttribute('href',url);
  await r.page.screenshot({path:artifactPath('full-title-no-excerpt.png'),fullPage:true});
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
