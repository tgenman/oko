"""Combined acceptance at HTTP: cost of selected data and unchanged delivery."""
import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from tests.support.reading_cost import library,traced_app
from tests.support.reading_measurements import ReadingMeasurements
from tests.support.reader import HEADERS


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
