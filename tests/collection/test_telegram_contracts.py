"""The shared cursor decoder is the explicit Source/Store history seam (#17)."""
import json

import pytest

from oko.collection.contracts import SourceError


def test_legacy_history_preserves_boundaries_and_only_current_enrollment():
    from oko.collection.telegram.contracts import HistoryEnvelope
    old = dict(after=240, upper=200, before=170, since=10.5, enrolled=3,
               lane='live', oldest=171, scan_complete=False,
               history_enroll=['album-7'],
               warnings=['partial'], history_error='retry', history_retry_at=99.0)
    cursor = HistoryEnvelope.decode(json.dumps(old), adapter='client')
    assert cursor.selection.enrolled_ids == ('album-7',)
    assert cursor.selection.error == 'retry'
    assert cursor.selection.retry_at == 99.0
    assert cursor.selection.oldest == 171
    assert cursor.position == dict(after=240, upper=200, before=170, since=10.5, enrolled=3, lane='live')
    restored = HistoryEnvelope.decode(cursor.encode(), adapter='client')
    assert restored == cursor
    assert old['history_enroll'] == ['album-7']


@pytest.mark.parametrize('payload,adapter', [
    ({'format_version': 2, 'adapter': 'web', 'selection': {}, 'position': {}}, 'web'),
    ({'format_version': 1, 'adapter': 'web', 'selection': {}, 'position': {}}, 'client'),
    ({'after': 20, 'since': 1}, 'web'),
    ({'format_version': 1, 'adapter': 'client', 'selection': {'scan_complete': 'false'}, 'position': {}}, 'client'),
    ([], 'client'),
])
def test_incompatible_cursor_is_visible_instead_of_resetting_history(payload, adapter):
    from oko.collection.telegram.contracts import HistoryEnvelope
    with pytest.raises(SourceError, match='курсор'):
        HistoryEnvelope.decode(json.dumps(payload), adapter=adapter)


def test_older_legacy_manifest_keeps_its_selected_ids():
    from oko.collection.telegram.contracts import HistoryEnvelope
    result = HistoryEnvelope.decode('{"after":90,"since":0,"history_ids":["77"],"scan_complete":false}', adapter='client')
    assert result.selection.enrolled_ids == ('77',)
    assert result.selection.scan_complete is False
