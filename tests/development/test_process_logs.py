"""Subprocess startup failures retain useful diagnostics across retries."""
import pytest

from tests.support.processes import launch, connect, cleanup


def test_failed_startup_keeps_separate_logs_and_reports_stderr(tmp_path):
    paths = []
    for value in ('invalid-first', 'invalid-second'):
        process = launch(tmp_path, lease=value)
        try:
            assert process.wait(timeout=10) != 0
            with pytest.raises(AssertionError, match='server exited before readiness') as failure:
                connect(tmp_path, process)
            log = process.log_path
            paths.append(log)
            assert str(log) in str(failure.value)
            assert value in str(failure.value)
            assert '--lease' in log.read_text()
            assert value in log.read_text()
        finally:
            cleanup(process)
    assert paths[0] != paths[1]
    assert 'invalid-first' in paths[0].read_text()
