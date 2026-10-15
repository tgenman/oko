import type {HistoryState} from '../api/generated/types.gen';
export function historyProgress(history: HistoryState | null | undefined) {
  if (!history || history.state==='absent') return '';
  const {selection,progress:counts}=history;
  const progress = selection.mode === 'count' ? `Загружено ${counts.loaded} из ${selection.count}` : `Загружено ${counts.loaded} публикаций с ${selection.date.split('-').reverse().join('.')}`;
  const state = history.state === 'cancelled' ? 'Загрузка истории остановлена' : history.state === 'waiting' ? 'Подбор не завершён — нажмите «Продолжить историю»' : history.state === 'paused' ? 'Начальная история приостановлена' : history.state === 'discovering' ? 'Выбираются публикации начальной истории' : history.state === 'not_started' ? 'Ожидает первого обращения к источнику' : history.state === 'complete' ? 'Начальная история завершена' : 'Начальная история загружается';
  const short = counts.scan_complete && counts.manifest_complete && selection.mode === 'count' && BigInt(selection.count || 0) > BigInt(counts.available) ? ` · Запрошено ${selection.count}, доступно ${counts.available}` : '';
  const outcomes = (counts.incomplete ? ` · ${counts.incomplete} неполных` : '') + (counts.retrying ? ` · ${counts.retrying} ожидает повторной попытки` : '') + (counts.unavailable ? ` · ${counts.unavailable} окончательно недоступно` : '');
  const errors = counts.errors?.length ? ' · ' + [...new Set(counts.errors.map(error => error.message))].join(' · ') : '';
  const discovery = counts.discovery_problem?.message ? ' · ' + counts.discovery_problem?.message + (counts.discovery_problem?.retry_at ? ' · повторная попытка после ' + new Date(counts.discovery_problem.retry_at).toLocaleString() : '') : '';
  return progress + ' · ' + state + outcomes + errors + discovery + short + (counts.warnings?.length ? ' · ' + counts.warnings.join(' · ') : '');
}
