"""Own an invocation, its process groups, scratch and lock across parent death.

A durable marker survives a supervisor crash. The next invocation fails closed
until an operator checks the recorded owned process groups and removes it.
"""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import threading
import time
from multiprocessing.connection import Connection
from typing import Any, Callable

from oko.processing.codex import CodexClient
from oko.processing.contracts import ProcessingError
from .protocol import JobRequest, LEASE_SECONDS, MAX_RESULT_BYTES


def supervise(control: Connection, root_name: str, request_data: dict[str, Any],
              factory: Callable[[], CodexClient]) -> None:
    root = Path(root_name)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (root / 'execution.lock').open('a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        control.send({'state': 'error', 'error': 'Codex: previous process still owns execution lock'})
        return
    marker = root / 'active.json'
    if marker.exists():
        control.send({'state': 'error', 'error': 'Codex: supervisor recovery requires verified process cleanup'})
        return
    request = JobRequest.model_validate(request_data)
    work = root / ('job-' + str(os.getpid()))
    work.mkdir(mode=0o700)
    groups: dict[int, asyncio.subprocess.Process] = {}
    marker.write_text(json.dumps({'supervisor': os.getpid(), 'groups': []}))
    marker.chmod(0o600)
    lease_until = time.time() + LEASE_SECONDS
    mono_until = time.monotonic() + LEASE_SECONDS
    finished = threading.Event()
    original_spawn = asyncio.create_subprocess_exec

    async def owned_spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await original_spawn(*args, **kwargs)
        # Existing LocalCodex uses a new session for every subprocess.
        if kwargs.get('start_new_session'):
            groups[process.pid] = process
            marker.write_text(json.dumps({'supervisor': os.getpid(), 'groups': sorted(groups)}))
        return process

    def group_exists(pid: int) -> bool:
        try:
            os.killpg(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            # Permission or another uncertain kernel result is not proof of exit.
            return True

    async def retire_exited_groups() -> None:
        # Retain actual owned Process objects while alive. Drop a group ID as
        # soon as its leader is reaped and the whole group has disappeared, so
        # later probes do not retain known-stale PGIDs throughout the request.
        while True:
            for pid, process in list(groups.items()):
                if process.returncode is not None and not group_exists(pid):
                    del groups[pid]
                    marker.write_text(json.dumps({'supervisor': os.getpid(), 'groups': sorted(groups)}))
            await asyncio.sleep(.02)

    async def execute() -> Any:
        adapter = factory()
        if request.operation == 'check':
            return await adapter.check()
        if request.operation == 'catalog':
            return await adapter.catalog()
        status = await adapter.check()
        if not status.get('available') or not status.get('limits_checked'):
            raise ProcessingError(str(status.get('reason') or 'Codex admission check failed'))
        assert request.choice is not None
        catalog = await adapter.catalog()
        model = next((row for row in catalog if row['model'] == request.choice.model), None)
        if model is None or request.choice.reasoning_effort not in model['reasoning_efforts'] or (
                request.choice.fast and not model['fast_supported']):
            raise ProcessingError('Codex choice is absent from the verified catalog')
        return await adapter.complete(request.prompt, request.schema_, work / 'invocation', request.choice)

    async def run() -> None:
        nonlocal lease_until, mono_until
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(execute())
        reaper = asyncio.create_task(retire_exited_groups())
        def monitor() -> None:
            nonlocal lease_until, mono_until
            while not finished.is_set():
                # Both clocks: a resumed Mac cannot renew a lease which expired asleep.
                if time.time() >= min(lease_until, request.deadline) or time.monotonic() >= mono_until:
                    loop.call_soon_threadsafe(task.cancel)
                    return
                try:
                    if control.poll(.1):
                        message = control.recv()
                        if message == 'renew':
                            if time.time() >= lease_until or time.monotonic() >= mono_until:
                                loop.call_soon_threadsafe(task.cancel)
                                return
                            lease_until = time.time() + LEASE_SECONDS
                            mono_until = time.monotonic() + LEASE_SECONDS
                        else:
                            loop.call_soon_threadsafe(task.cancel)
                            return
                except (EOFError, OSError):
                    loop.call_soon_threadsafe(task.cancel)
                    return
        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            result = await task
            if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
                raise ProcessingError('Codex response exceeded protocol limit')
            outcome = {'state': 'completed', 'result': result}
        except asyncio.CancelledError:
            outcome = {'state': 'cancelled'}
        except ProcessingError as error:
            outcome = {'state': 'error', 'error': error.label}
        except Exception:
            # Never include prompts, response bodies or raw subprocess diagnostics.
            outcome = {'state': 'error', 'error': 'Codex invocation failed'}
        finally:
            finished.set()
            watcher.join(1)
        reaper.cancel()
        await asyncio.gather(reaper, return_exceptions=True)
        for pid in groups:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                # Keep the marker below when termination cannot be confirmed.
                pass
        # SIGKILL delivery does not prove exit. Allow the child watcher to reap
        # owned leaders and require every tracked group to disappear before
        # clearing the recovery barrier or returning a successful result.
        stop_deadline = time.monotonic() + 2
        while any(group_exists(pid) for pid in groups) and time.monotonic() < stop_deadline:
            await asyncio.sleep(.02)
        if any(group_exists(pid) for pid in groups):
            outcome = {'state': 'error', 'error': 'Codex process stop not confirmed; recovery required'}
        else:
            shutil.rmtree(work, ignore_errors=False)
            marker.unlink()
        # Success/cancellation confirms group termination and scratch cleanup;
        # an uncertain stop retains both the marker and owned scratch.
        try:
            control.send(outcome)
        except (BrokenPipeError, EOFError, OSError):
            pass

    asyncio.create_subprocess_exec = owned_spawn
    try:
        asyncio.run(run())
    finally:
        control.close()
        lock.close()
