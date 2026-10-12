const byId = id => document.getElementById(id);

export function bindInitialHistory(prefix) {
  const mode = byId(prefix + '-history-mode');
  const count = byId(prefix + '-history-count'), date = byId(prefix + '-history-date');
  const now = new Date();
  date.max = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  function update() {
    const dated = mode.value === 'date';
    count.parentElement.hidden = dated; count.disabled = dated; count.required = !dated;
    date.parentElement.hidden = !dated; date.disabled = !dated; date.required = dated;
  }
  mode.onchange = update; update();
  return () => {
    if (!(mode.value === 'date' ? date : count).reportValidity()) return null;
    const whole = /^\d+$/.test(count.value);
    if (mode.value === 'count' && !whole && !Number.isSafeInteger(Number(count.value))) {
      count.setCustomValidity('Введите целое количество публикаций от 1'); count.reportValidity(); count.setCustomValidity(''); return null;
    }
    return {mode: mode.value, count: mode.value === 'count' ? (Number.isSafeInteger(Number(count.value)) ? Number(count.value) : count.value) : null,
      date: mode.value === 'date' ? date.value : null, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone};
  };
}

export function historyProgress(history) {
  if (!history) return '';
  const progress = history.mode === 'count' ? `Загружено ${history.loaded} из ${history.count_text || history.count}` : `Загружено ${history.loaded} публикаций с ${history.date.split('-').reverse().join('.')}`;
  const state = history.state === 'paused' ? 'Начальная история приостановлена' : history.state === 'pending' ? 'Ожидает первого обращения к источнику' : history.state === 'done' ? 'Начальная история завершена' : 'Начальная история загружается';
  const short = history.frozen && history.mode === 'count' && BigInt(history.count_text || history.count) > BigInt(history.available) ? ` · Запрошено ${history.count_text || history.count}, доступно ${history.available}` : '';
  return progress + ' · ' + state + short + (history.warnings?.length ? ' · ' + history.warnings.join(' · ') : '');
}
