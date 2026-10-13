"""An owned, bounded metadata-only history worker. No per-video extraction."""
import asyncio
import json
import sys
from typing import Any, Protocol

from oko.collection.contracts import SourceError


class HistoryClient(Protocol):
    async def fetch(self, channel: str, count: int, state: dict[str, Any]) -> dict[str, Any]: ...


class LocalHistory:
    async def fetch(self, channel: str, count: int, state: dict[str, Any]) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'oko.collection.youtube.history_worker',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        assert process.stdin and process.stdout
        try:
            async with asyncio.timeout(150):
                process.stdin.write(json.dumps(dict(channel=channel, count=count, state=state)).encode())
                await process.stdin.drain()
                process.stdin.close()
                raw = bytearray()
                while chunk := await process.stdout.read(65536):
                    raw.extend(chunk)
                    if len(raw) > 32_000_000:
                        raise SourceError('История превышает размер одной операции; уменьшите N')
                await process.wait()
            result: dict[str, Any] = json.loads(raw)
            if process.returncode or not isinstance(result, dict) or 'entries' not in result:
                raise ValueError('worker')
            return result
        except (ValueError, OSError, TimeoutError):
            raise SourceError('Не удалось подобрать историю YouTube; продолжите вручную') from None
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()
