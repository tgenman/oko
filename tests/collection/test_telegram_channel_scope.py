"""Telegram SDK scope: channels survive removal of groups and forum topics."""
from types import SimpleNamespace
import pytest
from oko.collection.telegram.worker import require_channel


@pytest.mark.parametrize('broadcast,forum', [(False,False),(False,True),(True,True)])
def test_group_and_forum_scope_is_rejected(broadcast,forum):
    with pytest.raises(ValueError,match='broadcast'):
        require_channel(SimpleNamespace(broadcast=broadcast,forum=forum),{})


def test_channel_without_topic_is_supported():
    require_channel(SimpleNamespace(broadcast=True,forum=False),{})


def test_channel_cannot_take_a_topic_boundary():
    with pytest.raises(ValueError,match='Topics'):
        require_channel(SimpleNamespace(broadcast=True,forum=False),{'topic_id':1})
