"""Observe real reader passes after moving only a synthetic scenario's clock."""
import asyncio
import time


class ReaderClock:
    def __init__(self):
        self.offset = 0.

    def __call__(self):
        return time.monotonic() + self.offset

    def advance_and_wait(self, client, seconds=0.):
        async def observe():
            runtime = client.app.state.host.execution
            original = runtime.reader_advance
            assert original is not None
            try:
                # A second pass admits tasks scheduled by the first. Each
                # callback runs in the actual Runtime loop, never in HTTP GET.
                for delta in (max(.3, seconds), .3):
                    ready = asyncio.get_running_loop().create_future()
                    def progressed():
                        original()
                        if not ready.done():
                            ready.set_result(None)
                    runtime.reader_advance = progressed
                    self.offset += delta
                    await asyncio.wait_for(ready, timeout=2)
            finally:
                runtime.reader_advance = original
        client.portal.call(observe)
