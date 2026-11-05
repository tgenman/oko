"""A new publication cannot silently discard an obsolete stored field."""
import pytest
from oko.storage.publication_document import stored_content


@pytest.mark.parametrize('extra',[
    {'_reader':{'title':'old'}}, {'_collection_revision':'old'},
    {'_input_hash':'old'}, {'_links':['old']}, {'_media_urls':{'x':'old'}},
    {'source_manifest':{}}, {'completeness':'old'},
    {'telegram':{'description':'old'}}, {'youtube':{'captions':'old'}},
])
def test_rejects_obsolete_projection(extra):
    with pytest.raises(ValueError,match='Obsolete'):
        stored_content({'blocks':[],'extra':extra})


def test_preserves_allowed_adapter_data():
    value={'blocks':[],'extra':{'telegram':{'members':[1],'album_id':'x'}}}
    assert stored_content(value)==value


@pytest.mark.parametrize('value',[None,[],42])
def test_rejects_malformed_platform_extra(value):
    with pytest.raises(ValueError,match='must be an object'):
        stored_content({'blocks':[],'extra':{'telegram':value}})
