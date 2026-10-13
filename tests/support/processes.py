import json, os, subprocess, sys, time, httpx
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def launch(root: Path, fault: str = '', lease: str = '6', grace: str = '15', *, model_origin: str = ''):
    env = dict(os.environ, PYTHONPATH=str(ROOT), OKO_TEST_FAULT=fault, OKO_TEST_MODEL_ORIGIN=model_origin)
    return subprocess.Popen([sys.executable, '-m', 'tests.process_server', '--data', str(root), '--no-browser', '--demo', '--lease', lease, '--grace', grace], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def connect(root: Path, process):
    for _ in range(250):
        try:
            metadata = json.loads((root / 'runtime.json').read_text())
            client = httpx.Client(base_url=metadata['url'], headers={'Authorization': 'Bearer ' + metadata['token']}, timeout=2)
            client.get('/api/status').raise_for_status()
            client.close()
            return httpx.Client(base_url=metadata['url'], headers={'Authorization': 'Bearer ' + metadata['token']}, timeout=2)
        except (OSError, ValueError, httpx.HTTPError):
            assert process.poll() is None, 'server exited before readiness'
            time.sleep(.02)
    raise AssertionError('server not ready')


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
