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
        checkout={'status':'FAIL','changed_files':['docs/report.md']},
        test_groups={'Node':dict(status='PASS',counts={'total':3,'passed':3},duration_seconds=1.2)})))
    result = subprocess.run([sys.executable, str(CLI), str(report), '--json'], text=True, capture_output=True)
    assert result.returncode == 1
    value = json.loads(result.stdout)
    assert value['suites']['python-tests']['failures'] == ['test_example.py::test_retry']
    assert value['suites']['browser']['status'] == 'PASS (1.20s)'
    assert value['checkout']['changed_files'] == ['docs/report.md']
    assert 'commands' not in value['suites']['browser']
    assert value['duration_seconds'] == 2.5
    assert value['test_groups']['Node']==dict(status='PASS',counts={'total':3,'passed':3},duration_seconds=1.2)


def test_saved_report_retains_early_failure_reason_log_and_retry(tmp_path):
    report=tmp_path/'report.json'
    detail=dict(test='tests/test_example.py::test_retry',reason='expected detail',log='/tmp/shard.log',retry='./scripts/check python --files tests/test_example.py::test_retry')
    report.write_text(json.dumps(dict(exit_code=1,duration_seconds=2,log='/tmp/full.log',suites={'python-tests':dict(status='FAIL',failures=[detail['test']],failure_details=[detail])})))
    result=subprocess.run([sys.executable,str(CLI),str(report),'--json'],text=True,capture_output=True)
    assert result.returncode==1
    assert json.loads(result.stdout)['suites']['python-tests']['failure_details']==[detail]
    result=subprocess.run([sys.executable,str(CLI),str(report)],text=True,capture_output=True)
    assert 'expected detail' in result.stdout and 'Retry:' in result.stdout and '/tmp/shard.log' in result.stdout
