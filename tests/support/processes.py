import json, os, subprocess, sys, time, httpx
from pathlib import Path
from tempfile import NamedTemporaryFile


ROOT = Path(__file__).resolve().parents[2]


class ServerProcess(subprocess.Popen):
    log_path: Path


def launch(root: Path, fault: str = '', lease: str = '6', grace: str = '15', *, model_origin: str = ''):
    env = dict(os.environ, PYTHONPATH=str(ROOT), OKO_TEST_FAULT=fault, OKO_TEST_MODEL_ORIGIN=model_origin)
    logs = root / 'process-logs'
    logs.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=logs, prefix='server-', suffix='.log', delete=False) as output:
        process = ServerProcess([sys.executable, '-m', 'tests.process_server', '--data', str(root), '--no-browser', '--demo', '--lease', lease, '--grace', grace], env=env, stdout=output, stderr=subprocess.STDOUT)
        process.log_path = Path(output.name)
    return process


def startup_failure(message: str, process: ServerProcess) -> AssertionError:
    with process.log_path.open('rb') as output:
        output.seek(0, os.SEEK_END)
        output.seek(max(0, output.tell() - 8192))
        tail = output.read(8192).decode('utf-8', errors='replace')
    return AssertionError(f'{message} (pid={process.pid}, exit={process.poll()})\nLog: {process.log_path}\n{tail}')


def connect(root: Path, process):
    for _ in range(250):
        try:
            metadata = json.loads((root / 'runtime.json').read_text())
            with httpx.Client(base_url=metadata['url'], headers={'Authorization': 'Bearer ' + metadata['token']}, timeout=2) as client:
                client.get('/api/status').raise_for_status()
            return httpx.Client(base_url=metadata['url'], headers={'Authorization': 'Bearer ' + metadata['token']}, timeout=2)
        except (OSError, ValueError, httpx.HTTPError):
            if process.poll() is not None:
                raise startup_failure('server exited before readiness', process)
            time.sleep(.02)
    raise startup_failure('server not ready', process)


def wait_cards(client, count):
    for _ in range(250):
        cards = client.get('/api/cards').json()
        if len(cards) == count and all(c['complete'] for c in cards):
            return cards
        time.sleep(.02)
    raise AssertionError(cards)


def cleanup(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
