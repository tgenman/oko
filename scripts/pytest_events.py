"""Emit compact failure records while pytest keeps its normal full report."""
import json


def failure(report):
    if not report.failed:
        return
    crash = getattr(report.longrepr, 'reprcrash', None)
    reason = getattr(crash, 'message', None) or str(report.longrepr).splitlines()[0]
    print('\nOKO_TEST_FAILURE '+json.dumps(dict(test=report.nodeid, reason=reason[:600]), ensure_ascii=False), flush=True)


def pytest_runtest_logreport(report):
    failure(report)


def pytest_collectreport(report):
    failure(report)
