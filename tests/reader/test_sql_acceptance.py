"""Combined acceptance at HTTP: cost of selected data and unchanged delivery."""
import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from tests.support.reading_cost import library,traced_app
from tests.support.reading_measurements import ReadingMeasurements
from tests.support.reader import HEADERS


@pytest.mark.parametrize('count',[26,100])
def test_complete_http_cost_matrix(tmp_path,count):
    feeds,items=library(tmp_path,[count]*4)
    app=traced_app(tmp_path)
    records=[]
    with TestClient(app,headers=HEADERS) as client,ReadingMeasurements(client,app,tmp_path) as measure:
        nav,cost=measure.request('navigation','GET','/api/v3/navigation');records.append(cost)
        folder=next(folder['id'] for folder in nav.json()['folders'] if any(feed['id']==feeds[0] for feed in folder['feeds']))
        operations=[('feed_cards','GET','/api/v3/cards',dict(params={'feed':feeds[0]})),
            ('all_cards','GET','/api/v3/cards',{}),('material','GET','/api/v3/materials/'+items[0],{}),
            ('status','GET','/api/v3/status',{}),('folder_preview','POST','/api/v3/folders/'+folder+'/completion/preview',{})]
        for name,method,url,options in operations:
            _,cost=measure.request(name,method,url,**options);records.append(cost)
            if name=='status':
                _,warm=measure.request('status_warm','GET',url);records.append(warm)
                assert warm['content_opens']==0
        first_page,first_cost=measure.request('cards_initial','GET','/api/v3/cards');records.append(first_cost)
        assert len(first_page.json()['items'])==40
        assert first_page.json()['total']==count*4
        unchanged,conditional_cost=measure.request('cards_unchanged','GET','/api/v3/cards',expected_status=304,headers={'If-None-Match':first_page.headers['ETag']});records.append(conditional_cost)
        assert unchanged.status_code==304 and conditional_cost['content_opens']==0 and conditional_cost['read_bytes']==0
        for url in ('/api/v3/status','/api/v3/navigation','/api/v3/publication/'+items[0]):
            initial,cost=measure.request('initial','GET',url);records.append(cost)
            for _ in range(2):
                reply,cost=measure.request('unchanged','GET',url,expected_status=304,headers={'If-None-Match':initial.headers['ETag']});records.append(cost)
                assert reply.status_code==304 and not reply.content
                if '/publication/' not in url:
                    assert cost['content_opens']==0 and cost['content_stats']==0 and cost['read_bytes']==0
        _,cost=measure.request('viewed','POST','/api/v3/materials/'+items[0]+'/viewed');records.append(cost)
        assert client.get('/api/v3/materials/'+items[0]).status_code==404
    output=Path(os.environ.get('OKO_TEST_ARTIFACT_DIR',tmp_path));output.mkdir(parents=True,exist_ok=True)
    (output/f'matrix-{count*4}.json').write_text(json.dumps(dict(total=count*4,records=records),indent=2))
    print('SQL_ACCEPTANCE',json.dumps(dict(total=count*4,records=records)))


def test_selected_cost_does_not_move_to_files_or_json(tmp_path):
    results=[]
    for others in [26,100]:
        root=tmp_path/str(others)
        feeds,_=library(root,[26,others,others,others])
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client,ReadingMeasurements(client,app,root) as measure:
            response,cost=measure.request('selected','GET','/api/v3/cards',params={'feed':feeds[0]})
            assert len(response.json()['items'])==26
            assert cost['content_opens']==0
            assert len(measure.files)==0
            results.append(cost)
    for key in ['sql','content_opens','content_stats','read_bytes','json_loads','json_input_bytes']:
        assert results[1][key]<=results[0][key]+2,(key,results)
    output=Path(os.environ.get('OKO_TEST_ARTIFACT_DIR',tmp_path));output.mkdir(parents=True,exist_ok=True)
    (output/'foreign-materials.json').write_text(json.dumps(results,indent=2))
    print('SCOPE_ACCEPTANCE',json.dumps(results))
