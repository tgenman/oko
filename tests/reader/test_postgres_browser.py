"""Required browser acceptance uses the same production reader over actual PostgreSQL."""
import os
from pathlib import Path
import shutil
import subprocess


def test_native_postgres_in_the_real_browser(postgres_url, tmp_path):
    root = Path(__file__).resolve().parents[2]
    credential = tmp_path / 'database-url'
    credential.write_text(postgres_url)
    credential.chmod(0o600)
    node = shutil.which('node')
    assert node is not None, 'Node.js is required for browser acceptance'
    artifacts = Path(os.environ.get('OKO_TEST_ARTIFACT_DIR', str(tmp_path / 'browser-artifacts')))
    result = subprocess.run([node, '--require', 'tsx/cjs', 'scripts/run-browser-tests.cts',
        '--require', 'tsx/cjs', '--require', './scripts/register-browser-typescript.cts',
        '--enable-source-maps', '--test', '--test-reporter=tap', 'tests/browser/test_postgres.ts'],
        cwd=root, env={**os.environ, 'OKO_TEST_DATABASE_URL_FILE': str(credential),
                       'OKO_TEST_ARTIFACT_DIR': str(artifacts)},
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'postgres-native-reader.png' in {path.name for path in artifacts.rglob('*.png')}
