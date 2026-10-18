import {serviceText} from '../service-text';
import type {HistoryState} from '../api/generated/types.gen';
export function historyProgress(history: HistoryState | null | undefined) {
  if (!history || history.state==='absent') return '';
  const {selection,progress:counts}=history;
  const progress = selection.mode === 'count' ? `Loaded ${counts.loaded} of ${selection.count}` : `Loaded ${counts.loaded} publications since ${selection.date.split('-').reverse().join('.')}`;
  const state = history.state === 'cancelled' ? 'History download stopped' : history.state === 'waiting' ? 'Discovery incomplete — click “Resume History”' : history.state === 'paused' ? 'Initial history paused' : history.state === 'discovering' ? 'Discovering initial history' : history.state === 'not_started' ? 'Waiting for the first source request' : history.state === 'complete' ? 'Initial history complete' : 'Loading initial history';
  const short = counts.scan_complete && counts.manifest_complete && selection.mode === 'count' && BigInt(selection.count || 0) > BigInt(counts.available) ? ` · Requested ${selection.count}, available ${counts.available}` : '';
  const outcomes = (counts.incomplete ? ` · ${counts.incomplete} incomplete` : '') + (counts.retrying ? ` · ${counts.retrying} awaiting retry` : '') + (counts.unavailable ? ` · ${counts.unavailable} permanently unavailable` : '');
  const errors = counts.errors?.length ? ' · ' + [...new Set(counts.errors.map(error => serviceText(error.message)))].join(' · ') : '';
  const discovery = counts.discovery_problem?.message ? ' · ' + serviceText(counts.discovery_problem.message) + (counts.discovery_problem?.retry_at ? ' · retry after ' + new Date(counts.discovery_problem.retry_at).toLocaleString() : '') : '';
  return progress + ' · ' + state + outcomes + errors + discovery + short + (counts.warnings?.length ? ' · ' + counts.warnings.map(serviceText).join(' · ') : '');
}
