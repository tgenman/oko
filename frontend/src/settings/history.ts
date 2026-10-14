import type {History} from '../api/generated/types.gen';
export function historyProgress(history: History | null | undefined) {
  if (!history) return '';
  const progress = history.mode === 'count' ? `Загружено ${history.loaded} из ${history.count_text || history.count}` : `Загружено ${history.loaded} публикаций с ${(history.date||'').split('-').reverse().join('.')}`;
  const state = history.state === 'cancelled' ? 'Загрузка истории остановлена' : history.state === 'waiting' ? 'Подбор не завершён — нажмите «Продолжить историю»' : history.state === 'paused' ? 'Начальная история приостановлена' : history.state === 'selecting' ? 'Выбираются публикации начальной истории' : history.state === 'pending' ? 'Ожидает первого обращения к источнику' : history.state === 'done' ? 'Начальная история завершена' : 'Начальная история загружается';
  const short = history.scan_complete && history.frozen && history.mode === 'count' && BigInt(history.count_text || history.count || 0) > BigInt(history.available) ? ` · Запрошено ${history.count_text || history.count}, доступно ${history.available}` : '';
  const outcomes = (history.incomplete ? ` · ${history.incomplete} неполных` : '') + (history.retrying ? ` · ${history.retrying} ожидает повторной попытки` : '') + (history.unavailable ? ` · ${history.unavailable} окончательно недоступно` : '');
  const errors = history.errors?.length ? ' · ' + [...new Set(history.errors.map(error => error.message))].join(' · ') : '';
  const discovery = history.discovery_error ? ' · ' + history.discovery_error + (history.discovery_retry_at ? ' · повторная попытка после ' + new Date(history.discovery_retry_at * 1000).toLocaleString() : '') : '';
  return progress + ' · ' + state + outcomes + errors + discovery + short + (history.warnings?.length ? ' · ' + history.warnings.join(' · ') : '');
}
