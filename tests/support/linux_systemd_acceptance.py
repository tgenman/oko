"""Opt-in root acceptance on the dedicated Oko VM; kills only its synthetic unit."""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid

import httpx

UNIT = 'oko-codex-test.service'
ROOT = Path('/run/oko-codex-test')


def systemctl(*args):
    return subprocess.check_output(['systemctl', *args], text=True).strip()


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except FileNotFoundError:
        return None


def wait(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:return value
        time.sleep(.1)
    raise AssertionError('Owned synthetic unit did not reach expected state')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Root required on the dedicated Linux test host')
    template = Path('/etc/systemd/system/oko-codex.service').read_text()
    fixture = Path(__file__).with_name('linux_helper_fixture.py')
    project = Path(__file__).resolve().parents[2]
    lines = []
    for line in template.splitlines():
        if line.startswith('ExecStart='):
            line = f'Environment=PYTHONPATH={project}\nExecStart={project}/.venv/bin/python {fixture}'
        elif line.startswith('RuntimeDirectory='):
            line = 'RuntimeDirectory=oko-codex-test'
        elif line.startswith('ReadWritePaths='):
            line = 'ReadWritePaths=/var/lib/oko-codex /run/oko-codex-test'
        lines.append(line)
    path = Path('/run/systemd/system') / UNIT
    if path.exists():raise SystemExit('Refusing to replace an existing synthetic unit')
    path.write_text('\n'.join(lines)+'\n')
    token = Path('/var/lib/oko-codex/bridge-token').read_text().strip()
    headers = {'Authorization':'Bearer '+token, 'X-Oko-Protocol':'1'}
    try:
        systemctl('daemon-reload')
        systemctl('start', UNIT)
        for case in ('helper-parent', 'job-supervisor', 'cancel-then-supervisor'):
            wait(lambda: (ROOT/'helper.sock').exists())
            with httpx.Client(transport=httpx.HTTPTransport(uds=str(ROOT/'helper.sock')), base_url='http://localhost', headers=headers, timeout=2) as client:
                health = client.get('/v1/health').json()
                client.headers.update({'X-Oko-Boot':health['boot_id'],'X-Oko-Session':uuid.uuid4().hex})
                identifier = uuid.uuid4().hex
                body = dict(boot_id=health['boot_id'],session_id=client.headers['X-Oko-Session'], operation='complete',
                            deadline=time.time()+120,prompt='synthetic',schema={},choice=dict(model='synthetic',reasoning_effort='low',fast=False))
                result = client.put('/v1/jobs/'+identifier,json=body)
                assert result.status_code == 200, result.text
                wait(lambda: (ROOT/'owned-child').exists())
                marker = json.loads((ROOT/'jobs/active.json').read_text())
                parent = int(systemctl('show',UNIT,'--property=MainPID','--value'))
                owned = {pid:identity(pid) for pid in (parent,marker['supervisor'],int((ROOT/'owned-child').read_text()))}
                cancellation = None
                if case == 'cancel-then-supervisor':
                    def cancel_request():
                        try:client.delete('/v1/jobs/'+identifier)
                        except httpx.HTTPError:pass  # Restart may lose the cancellation acknowledgement.
                    cancellation=threading.Thread(target=cancel_request,daemon=True)
                    cancellation.start()
                    wait(lambda: (ROOT/'cancel-seen').exists())
                target = parent if case=='helper-parent' else marker['supervisor']
                os.kill(target,signal.SIGKILL)
                def recovered():
                    pid=int(systemctl('show',UNIT,'--property=MainPID','--value'))
                    if not pid or pid==parent or not (ROOT/'helper.sock').exists():return False
                    if any(start is not None and identity(old)==start for old,start in owned.items()):return False
                    try:
                        current=client.get('/v1/health')
                        return current.status_code==200 and current.json()['boot_id']!=health['boot_id']
                    except httpx.HTTPError:return False
                wait(recovered)
                if cancellation is not None:
                    cancellation.join(5)
                    assert not cancellation.is_alive()
                assert not (ROOT/'jobs/active.json').exists()
                print(json.dumps({'case':case,'old_generation_gone':True,'new_generation_healthy':True}),flush=True)
    finally:
        subprocess.run(['systemctl','stop',UNIT],check=False,capture_output=True)
        path.unlink(missing_ok=True)
        systemctl('daemon-reload')


if __name__=='__main__':
    main()
