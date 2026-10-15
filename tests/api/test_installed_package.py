"""Package acceptance: an installed wheel runs without checkout/Node."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from email.parser import Parser

from tests.support.install_wheel import install_wheel

ROOT = Path(__file__).resolve().parents[2]


def test_wheel_runs_outside_checkout_without_node(tmp_path):
    built = subprocess.run([sys.executable,str(ROOT/'scripts/build-package.py'),'--output',str(tmp_path/'wheels')],cwd=ROOT,capture_output=True,text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    wheels = list((tmp_path/'wheels').glob('*.whl'))
    assert len(wheels) == 1
    installed = tmp_path/'installed'
    with zipfile.ZipFile(wheels[0]) as package:
        names = package.namelist()
        assert any('/docs/swagger-ui-bundle.js' in name for name in names)
        assert 'oko/reader/generated/openapi.json' in names
        metadata = Parser().parsestr(package.read(next(name for name in names if name.endswith('.dist-info/METADATA'))).decode())
        assert 'dev' not in metadata.get_all('Provides-Extra', [])
        requirements = metadata.get_all('Requires-Dist', [])
        assert not any(name in requirement for requirement in requirements for name in ('pytest', 'poetry-core', 'setuptools', 'datamodel-code-generator'))
    install_wheel(wheels[0], installed)
    script = """
import json,tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from oko.reader.static import validate_frontend
assert not (Path.cwd()/'frontend').exists()
root=validate_frontend()
with TestClient(create_app(Path(tempfile.mkdtemp()),token='synthetic')) as client:
    assert client.get('/').status_code==200
    assert client.get('/docs').status_code==200
    assert client.get('/docs/swagger-ui-bundle.js').status_code==200
    assert client.get('/openapi.json').json()['openapi']=='3.1.0'
    assert client.get('/api/status',headers={'Authorization':'Bearer synthetic'}).json()['live_tabs']==0
print('Installed wheel HTTP smoke passed')
"""
    result=subprocess.run([sys.executable,'-c',script],cwd=tmp_path,env={'PYTHONPATH':str(installed),'PATH':''},capture_output=True,text=True)
    assert result.returncode == 0,result.stdout+result.stderr
    assert 'Installed wheel HTTP smoke passed' in result.stdout
