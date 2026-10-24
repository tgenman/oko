"""An opened media descriptor remains readable after its publication is removed."""
from collections.abc import Iterator
import os
from typing import BinaryIO

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


class OpenMediaResponse(StreamingResponse):
    def __init__(self, file: BinaryIO, mime: str) -> None:
        self.file = file
        super().__init__(self.chunks(), media_type=mime,
            headers={'Content-Disposition': 'inline', 'Content-Length': str(os.fstat(file.fileno()).st_size)})

    def chunks(self) -> Iterator[bytes]:
        while chunk := self.file.read(65536):
            yield chunk

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.file.close()
