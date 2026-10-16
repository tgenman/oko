"""Assertions of canonical v3 responses against immutable historical data fixtures."""


def saved_theses(actual, expected, provenance):
    result=actual['theses']['result']
    assert result['points']==[{'text':point['text'],'references':[{'material_id':ref['id'],'source_url':ref['url']} for ref in point['sources']]} for point in expected['points']]
    origin=result['provenance']
    assert origin['kind']=='local'
    assert origin['processor_id']==provenance['processor']
    assert origin['model']=={'state':'known','name':provenance['model']}


def saved_material(actual, expected):
    assert actual['availability']=='available'
    content=actual['content']
    assert content['text']==expected['text']
    assert content['revision_id']==expected['version']
    assert content['images']==[url.replace('/api/materials/','/api/v3/materials/',1) for url in expected['images']]
    saved_theses(actual,expected['theses'],expected['provenance'])
    filtered=actual['filter']['result']
    assert {key:filtered[key] for key in ('hidden','restored','reason','removed')}=={key:expected['filter'][key] for key in ('hidden','restored','reason','removed')}
