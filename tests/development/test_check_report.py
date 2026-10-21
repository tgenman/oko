"""Compact report CLI preserves distinct suite and checkout outcomes."""
import json
from pathlib import Path
import subprocess
import sys

CLI = Path(__file__).resolve().parents[2] / 'scripts/check-report'


def test_report_json_excludes_command_inventories_but_keeps_failures(tmp_path):
    report = tmp_path / 'report.json'
    report.write_text(json.dumps(dict(exit_code=1, duration_seconds=2.5, log='/tmp/full.log',
        suites={'browser': {'status': 'PASS (1.20s)', 'failures': [], 'commands': [{'command':['very','long','inventory']}]},
                'python-tests': {'status':'FAIL (0.80s)', 'failures':['test_example.py::test_retry']}},
        checkout={'status':'FAIL','changed_files':['docs/report.md']})))
    result = subprocess.run([sys.executable, str(CLI), str(report), '--json'], text=True, capture_output=True)
    assert result.returncode == 1
    value = json.loads(result.stdout)
    assert value['suites']['python-tests']['failures'] == ['test_example.py::test_retry']
    assert value['suites']['browser']['status'] == 'PASS (1.20s)'
    assert value['checkout']['changed_files'] == ['docs/report.md']
    assert 'commands' not in value['suites']['browser']
    assert value['duration_seconds'] == 2.5


def test_report_text_accepts_historical_reports_without_checkout_field(tmp_path):
    report = tmp_path / 'report.json'
    report.write_text(json.dumps(dict(exit_code=0, duration_seconds=1, log='/tmp/full.log',
        suites={'browser': {'status':'PASS (1.00s)', 'failures':[]}})))
    result = subprocess.run([sys.executable, str(CLI), str(report)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'PASS (1.00s)' in result.stdout
    assert '/tmp/full.log' in result.stdout


def test_report_rejects_incomplete_json_without_traceback(tmp_path):
    report = tmp_path / 'report.json'
    report.write_text('{')
    result = subprocess.run([sys.executable, str(CLI), str(report)], text=True, capture_output=True)
    assert result.returncode == 2
    assert 'Traceback' not in result.stderr


def test_json_and_text_reject_wrong_field_types_before_printing(tmp_path):
    report = tmp_path/'report.json'
    report.write_text(json.dumps(dict(exit_code=0, duration_seconds=None,
        suites={'browser': {'status': None}}, log=None)))
    for options in ([], ['--json']):
        result = subprocess.run([sys.executable, str(CLI), str(report), *options], text=True, capture_output=True)
        assert result.returncode == 2
        assert result.stdout == ''
        assert 'Traceback' not in result.stderr


def test_compact_report_exposes_worker_times_and_slowest_files_and_tests(tmp_path):
    report=tmp_path/'report.json'
    report.write_text(json.dumps(dict(exit_code=1,duration_seconds=3.2,log='/tmp/full.log',suites={
        'python-tests':dict(status='FAIL (3.00s)',failures=['test_bad'],duration_seconds=3,
            worker_durations={'python-tests-1':1,'python-tests-2':3},
            file_durations={'tests/fast.py':.1,'tests/slow.py':2},
            test_durations=[dict(file='tests/slow.py',name='test_bad',seconds=2),dict(file='tests/fast.py',name='test_ok',seconds=.1)])})))
    result=subprocess.run([sys.executable,str(CLI),str(report),'--json'],capture_output=True,text=True)
    assert result.returncode==1
    suite=json.loads(result.stdout)['suites']['python-tests']
    assert suite['worker_durations']['python-tests-2']==3
    assert suite['slow_files'][0]==dict(file='tests/slow.py',seconds=2)
    assert suite['slow_tests'][0]['name']=='test_bad'
