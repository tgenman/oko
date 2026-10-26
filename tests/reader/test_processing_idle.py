"""An idle model worker must not keep opening SQL transactions at its active rate."""
import asyncio
import re

from oko.execution import Execution
from tests.support.storage import Store
from oko.workers.control import JobControl
from oko.workers.processing import FilterWorker
from tests.support.codex import CodexService
from tests.support.filters import transport


def test_idle_processing_worker_limits_database_claim_polling(tmp_path):
    store = Store(tmp_path, lambda _: None)
    execution = Execution(lambda: True)
    execution._store = store
    tasks = JobControl(execution, {}, None, store.clock)
    worker = FilterWorker(execution, tasks, store.clock, CodexService(), transport([]))
    claims = []
    store.db.set_trace_callback(lambda query: claims.append(query)
        if re.match(r"SELECT\s+\*\s+FROM\s+jobs\s+WHERE\s+status\s*=\s*'pending'", query) else None)

    async def observe():
        running = asyncio.create_task(worker.run())
        try:
            await asyncio.sleep(.65)
        finally:
            execution._stop_requested.set()
            await asyncio.wait_for(running, 1)
    try:
        asyncio.run(observe())
        assert claims, 'The observer must see an actual queue check'
        assert len(claims) <= 8, f'Idle worker made {len(claims)} database claim attempts in 650ms'
    finally:
        store.db.set_trace_callback(None)
        store.db.close()
