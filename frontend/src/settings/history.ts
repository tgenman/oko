import {serviceText} from '../service-text';
import type {HistoryState,HistoryStopReason} from '../api/generated/types.gen';
const stopReasons:Record<HistoryStopReason,string>={
 count_reached:'Requested number selected', date_boundary:'Reached the selected date; coverage of the period is not confirmed',
 no_continuation:'The source provides no continuation', archive_end:'The available archive chain ended',
 request_failed:'Could not load a history page; this attempt has ended', cycle:'History stopped: a continuation link forms a cycle',
 page_limit:'History stopped at the 1000-page limit', ambiguous_continuation:'History stopped: continuation links are ambiguous',
 cancelled:'History selection stopped',
};
export function historyProgress(history: HistoryState | null | undefined) {
  if (!history || history.state==='absent') return '';
  const {selection,progress:counts}=history;
  const progress = `${counts.stop_reason?`Found ${counts.available} of ${selection.count} · `:''}Loaded ${counts.loaded} of ${selection.count}`;
  const state = history.state === 'cancelled' ? 'History download stopped' : history.state === 'waiting' ? 'Discovery incomplete — refresh this feed' : history.state === 'paused' ? 'History waiting — refresh this feed' : history.state === 'discovering' ? 'Discovering initial history' : history.state === 'not_started' ? 'Waiting for the first source request' : history.state === 'complete' ? counts.stop_reason?'History selection finished':'Initial history complete' : 'Loading initial history';
  const short = counts.scan_complete && counts.manifest_complete && selection.mode === 'count' && BigInt(selection.count || 0) > BigInt(counts.available) ? ` · Requested ${selection.count}, available ${counts.available}` : '';
  const outcomes = (counts.incomplete ? ` · ${counts.incomplete} incomplete` : '') + (counts.retrying ? ` · ${counts.retrying} awaiting retry` : '') + (counts.unavailable ? ` · ${counts.unavailable} permanently unavailable` : '');
  const errors = counts.errors?.length ? ' · ' + [...new Set(counts.errors.map(error => serviceText(error.message)))].join(' · ') : '';
  const discovery = counts.discovery_problem?.message ? ' · ' + serviceText(counts.discovery_problem.message) + (counts.discovery_problem?.retry_at ? ' · retry after ' + new Date(counts.discovery_problem.retry_at).toLocaleString() : '') : '';
  return progress + ' · ' + state + (counts.stop_reason?' · '+stopReasons[counts.stop_reason]:'') + outcomes + errors + discovery + short + (counts.warnings?.length ? ' · ' + counts.warnings.map(serviceText).join(' · ') : '');
}
