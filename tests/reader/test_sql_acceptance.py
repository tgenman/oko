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
        nav,cost=measure.request('navigation','GET','/api/v3/reading-composition');records.append(cost)
        folder=next(f['feed']['folder_id'] for f in nav.json()['feeds'] if f['feed']['id']==feeds[0])
        operations=[('feed_cards','GET','/api/v3/cards',dict(params={'feed':feeds[0]})),
            ('all_cards','GET','/api/v3/cards',{}),('material','GET','/api/v3/materials/'+items[0],{}),
            ('status','GET','/api/v3/status',{}),('folder_preview','POST','/api/v3/folders/'+folder+'/completion/preview',{})]
        for name,method,url,options in operations:
            _,cost=measure.request(name,method,url,**options);records.append(cost)
            if name=='status':
                _,warm=measure.request('status_warm','GET',url);records.append(warm)
                assert warm['content_opens']==0
        options=dict(target=dict(scope='all'),sort='newest',show_hidden=False)
        initial,cost=measure.request('check_initial','POST','/api/v3/reading/check',json=dict(storage_id=None,resources=[dict(kind='status',known_version=None),dict(kind='navigation',known_version=None),dict(kind='list',options=options,known_version=None,known_cards={})]));records.append(cost)
        frame=initial.json()
        cards={r['id']:r['version'] for r in frame['resources'] if r['kind']=='card'}
        resources=[]
        for r in frame['resources']:
            if r['kind']=='list':resources.append(dict(kind='list',options=options,known_version=r['version'],known_cards=cards))
            elif r['kind']!='card':resources.append(dict(kind=r['kind'],**({'id':r['id']} if r['kind']=='feed' else {}),known_version=r['version']))
        opened,_=measure.request('open','GET','/api/v3/reading/materials/'+items[0])
        resources.extend(dict(kind=r['kind'],id=r['id'],known_version=r['version']) for r in opened.json()['resources'])
        for _ in range(2):
            reply,cost=measure.request('check_unchanged','POST','/api/v3/reading/check',json=dict(storage_id=frame['storage_id'],resources=resources));records.append(cost)
            assert all(r['state']=='unchanged' and 'data' not in r for r in reply.json()['resources'])
            assert cost['sql']<30 and cost['content_opens']==0 and cost['content_stats']==0 and cost['read_bytes']==0
            assert cost['json_loads']<20
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
            assert len(response.json())==26
            assert cost['content_opens']==26
            assert len(measure.files)==26
            results.append(cost)
    for key in ['sql','content_opens','content_stats','read_bytes','json_loads','json_input_bytes']:
        assert results[1][key]<=results[0][key]+2,(key,results)
    output=Path(os.environ.get('OKO_TEST_ARTIFACT_DIR',tmp_path));output.mkdir(parents=True,exist_ok=True)
    (output/'foreign-materials.json').write_text(json.dumps(results,indent=2))
    print('SCOPE_ACCEPTANCE',json.dumps(results))
