"""The shared cursor decoder is the explicit Source/Store history seam (#17)."""
import json

import pytest

from oko.collection.contracts import SourceError




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




def test_current_envelope_round_trip_keeps_selection_and_opaque_position():
    from oko.collection.telegram.contracts import HistoryEnvelope,HistorySelection
    value=HistoryEnvelope(adapter='client',selection=HistorySelection(enrolled_ids=('post-1',),scan_complete=False),position={'telegram_client_adapter':'opaque'})
    assert HistoryEnvelope.decode(value.encode(),adapter='client')==value
