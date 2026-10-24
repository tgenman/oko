"""Synthetic process-tree fixture for systemd Linux acceptance; never uses Codex auth."""
import asyncio
from pathlib import Path

from oko.processing.codex_bridge.__main__ import serve
from oko.processing.codex_bridge.protocol import BridgeConfig


class Sleeper:
    async def check(self):
        return dict(available=True, auth='synthetic', limits_checked=True)
    async def catalog(self):
        return [dict(model='synthetic', reasoning_efforts=['low'], fast_supported=False)]
    async def complete(self, prompt, schema, directory, choice):
        process = await asyncio.create_subprocess_exec('/bin/sleep', '60', start_new_session=True)
        Path('/run/oko-codex-test/owned-child').write_text(str(process.pid))
        try:
            await process.wait()
        except asyncio.CancelledError:
            Path('/run/oko-codex-test/cancel-seen').touch()
            await asyncio.sleep(5)
            raise
        return '{}'


if __name__ == '__main__':
    serve(BridgeConfig(token_file=Path('/var/lib/oko-codex/bridge-token'),
        scratch_root=Path('/run/oko-codex-test/jobs'), socket_path=Path('/run/oko-codex-test/helper.sock'),
        release='synthetic-systemd'), adapter_factory=Sleeper)
