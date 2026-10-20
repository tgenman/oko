// Translate built-in service presentation without changing saved diagnostics or source content.
const messages=new Map<string,string>([
 [
  "Сбор и обработка остановлены.",
  "Collection and processing are stopped."
 ],
 [
  "Тезисы пока не создаются.",
  "Summaries are not being generated yet."
 ],
 [
  "Сбор и отметки этого аккаунта приостановлены; локальные материалы доступны.",
  "Collection and read acknowledgements for this account are paused; local items remain available."
 ],
 [
  "Сохранённое содержимое остаётся доступным.",
  "Saved content remains available."
 ],
 [
  "Око продолжает попытки удаления.",
  "Oko continues trying to delete the data."
 ],
 [
  "Доступная часть остаётся пригодной для чтения.",
  "The available portion can still be read."
 ],
 [
  "Око сверяет результат; действие не повторяется автоматически.",
  "Oko is checking the result; the action will not be retried automatically."
 ],
 [
  "Не удалось подтвердить выполнение действия.",
  "Could not confirm that the action completed."
 ],
 [
  "Telegram: при сборе перечитываются известные непросмотренные сообщения и вложения. Отсутствующие сообщения сохраняются локально; исчезновение может означать удаление или потерю доступа.",
  "Telegram: known unread messages and attachments are checked again during collection. Missing messages are kept locally; their absence may mean deletion or lost access."
 ],
 [
  "Telegram web: видимая публичная история; скрытые сообщения и неизвестная смена username могут быть не обнаружены.",
  "Telegram web: visible public history only; hidden messages and unknown username changes may go undetected."
 ],
 [
  "YouTube: повторно проверяются название и превью видео в окне Atom. Полученные субтитры сохраняются без повторных запросов. Удаление видео и правки вне этого окна не определяются.",
  "YouTube: video titles and previews are checked again within the Atom window. Downloaded subtitles are kept without further requests. Deleted videos and changes outside this window are not detected."
 ],
 [
  "RSS: повторно проверяются статьи в доступном окне фида; 404/410 сохраняют локальную копию. Исчезновение из окна не означает удаление.",
  "RSS: articles are checked again within the available feed window; a local copy is kept after 404/410 responses. Disappearance from the window does not mean deletion."
 ],
 [
  "RSS отдаёт ограниченное окно: полнота истории за период не подтверждена",
  "RSS provides a limited window: full history coverage for the period is not confirmed"
 ],
 [
  "Сохранённая граница удалена или недоступна; история могла иметь пробел",
  "The saved boundary was deleted or is unavailable; history may contain a gap"
 ],
 [
  "Некорректный запрос",
  "Invalid request"
 ],
 [
  "Недопустимое значение",
  "Invalid value"
 ],
 [
  "Недопустимый адрес",
  "Address not allowed"
 ],
 [
  "Посторонняя страница",
  "Page not allowed"
 ],
 [
  "Требуется локальная сессия",
  "Local session required"
 ],
 [
  "Клиентская сессия истекла",
  "Client session expired"
 ],
 [
  "Контекст команды не совпадает",
  "Command context mismatch"
 ],
 [
  "Действие не выполнено",
  "Action did not complete"
 ],
 [
  "Настройки не найдены",
  "Settings not found"
 ],
 [
  "Фид не найден",
  "Feed not found"
 ],
 [
  "Аватар недоступен",
  "Avatar unavailable"
 ],
 [
  "Объект недоступен",
  "Resource unavailable"
 ],
 [
  "Версия недоступна",
  "Version unavailable"
 ],
 [
  "Вложение недоступно",
  "Attachment unavailable"
 ],
 [
  "Фид удаляется",
  "Feed is being deleted"
 ],
 [
  "Материал недоступен",
  "Item unavailable"
 ],
 [
  "Материал не найден",
  "Item not found"
 ],
 [
  "Хранилище не готово",
  "Storage not ready"
 ],
 [
  "Неверный адрес Telegram",
  "Invalid Telegram address"
 ],
 [
  "Привязку Telegram нельзя менять настройкой фида",
  "Telegram binding cannot be changed through feed settings"
 ],
 [
  "Способ доступа Telegram нельзя менять настройкой фида",
  "Telegram access method cannot be changed through feed settings"
 ],
 [
  "Веб-доступ не поддерживает отметки Telegram",
  "Web access does not support Telegram read acknowledgements"
 ],
 [
  "Для Око используется global",
  "Use global for Oko settings"
 ],
 [
  "Подтверждение устарело. Проверьте количество снова.",
  "Confirmation expired. Check the count again."
 ],
 [
  "Состав фида изменился. Подтвердите новое количество.",
  "Feed contents changed. Confirm the new count."
 ],
 [
  "Состав папки изменился. Подтвердите новое количество.",
  "Folder contents changed. Confirm the new count."
 ],
 [
  "Сначала проверьте текущий URL фида",
  "Check the current feed URL first"
 ],
 [
  "Для Telegram используйте подключение Telegram-источника",
  "Use the Telegram connection to add a Telegram source"
 ],
 [
  "Неверный URL фида",
  "Invalid feed URL"
 ],
 [
  "Проверка фида не завершилась за 25 секунд",
  "Feed check did not complete within 25 seconds"
 ],
 [
  "Проверка фида прервана; повторите действие",
  "Feed check interrupted; try again"
 ],
 [
  "llama.cpp не настроен",
  "llama.cpp is not configured"
 ],
 [
  "Проверка прервана",
  "Check interrupted"
 ],
 [
  "Загрузка каталога прервана",
  "Catalog loading interrupted"
 ],
 [
  "Сохранение настроек прервано",
  "Saving settings interrupted"
 ],
 [
  "Проверка Codex прервана",
  "Codex check interrupted"
 ],
 [
  "Обработанный материал недоступен",
  "Processed item unavailable"
 ],
 [
  "Фильтрация не включена",
  "Filtering is not enabled"
 ],
 [
  "Тезисы для материала не включены",
  "Summaries are not enabled for this item"
 ],
 [
  "Результат недоступен для пересоздания",
  "Result unavailable for regeneration"
 ],
 [
  "Некорректные настройки прокси: нужен HTTP/HTTPS-адрес без учётных данных, пути и параметров",
  "Invalid proxy settings: use an HTTP/HTTPS address without credentials, path or query parameters"
 ],
 [
  "Запрещена публичная AI-обработка; выберите локальную модель",
  "Public AI processing is not allowed; choose a local model"
 ],
 [
  "Telegram-сессия занята другим клиентом",
  "Telegram session is in use by another client"
 ],
 [
  "Укажите http(s) адрес сервера без пути и авторизации",
  "Enter an HTTP(S) server address without a path or credentials"
 ],
 [
  "Неверный порт",
  "Invalid port"
 ],
 [
  "Модель или API llama.cpp недоступны",
  "The model or llama.cpp API is unavailable"
 ],
 [
  "llama.cpp занят; ожидание повтора",
  "llama.cpp is busy; waiting to retry"
 ],
 [
  "Ошибка llama.cpp; проверьте подключение",
  "llama.cpp error; check the connection"
 ],
 [
  "Ответ llama.cpp слишком большой",
  "llama.cpp response is too large"
 ],
 [
  "llama.cpp вернул ошибку",
  "llama.cpp returned an error"
 ],
 [
  "llama.cpp недоступен; ожидание подключения",
  "llama.cpp unavailable; waiting for connection"
 ],
 [
  "Некорректный ответ llama.cpp",
  "Invalid llama.cpp response"
 ],
 [
  "Выбранная модель отсутствует в llama.cpp",
  "The selected model is missing from llama.cpp"
 ],
 [
  "Удалённая или непроверенная обработка: требуется разрешение публичного AI",
  "Remote or unverified processing: public AI permission required"
 ],
 [
  "Материал превышает лимит входа; текст не обрезан",
  "Item exceeds the input limit; text has not been truncated"
 ],
 [
  "llama.cpp не завершил результат",
  "llama.cpp did not finish the result"
 ],
 [
  "Результат не соответствует схеме обработки",
  "Result does not match the processing schema"
 ],
 [
  "Модель вернула недопустимое действие",
  "Model returned an invalid action"
 ],
 [
  "Не удалось получить каталог Codex; проверьте подключение",
  "Could not load the Codex catalog; check the connection"
 ],
 [
  "Око остановлено",
  "Oko stopped"
 ],
 [
  "Хранилище недоступно",
  "Storage unavailable"
 ],
 [
  "Локальная модель",
  "Local Model"
 ],
 [
  "Не выбрана модель Codex",
  "No Codex Model Selected"
 ],
["Внешнее встраивание не сохранено локально", "External embed not saved locally"],
["Файл статьи не предоставлен", "Article file not provided"],
["Telegram не предоставляет атомарный снимок истории: набор выбирается по доступным страницам; удалённые во время выбора публикации могли не попасть в него.", "Telegram does not provide an atomic history snapshot: items are selected from available pages; publications deleted during selection may be missing."],
["RSS: полнота истории за период не подтверждена", "RSS: history coverage for the period is not confirmed"],
["Страницы RSS могут меняться во время обхода: точность выбора последних N не подтверждена", "RSS pages may change during discovery: the accuracy of the latest N selection is not confirmed"],
["Порядок RSS неоднозначен: точность выбора последних N не подтверждена", "RSS order is ambiguous: the accuracy of the latest N selection is not confirmed"]
]);
export function serviceText(value:string):string {
 const exact=messages.get(value);if(exact)return exact;
 const subtitles=/^Ожидают субтитров: (\d+)\.$/.exec(value);if(subtitles)return `Waiting for subtitles: ${subtitles[1]}.`;
 return value.split(" · ").map(part=>messages.get(part)||part).join(" · ");
}
