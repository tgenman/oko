import asyncio
import pytest

from oko.reader.media_response import OpenMediaResponse


@pytest.mark.parametrize('fail_send', [False, True])
def test_open_media_survives_unlink_and_closes_on_send_failure(tmp_path, fail_send):
    path = tmp_path / 'media.bin'
    content = b'media' * 30000
    path.write_bytes(content)
    file = path.open('rb')
    response = OpenMediaResponse(file, 'application/octet-stream')
    path.unlink()  # A viewed publication can disappear before ASGI sends its body.
    chunks = []

    async def receive():
        raise AssertionError('ASGI 2.4 does not need a disconnect monitor')

    async def send(message):
        if message['type'] == 'http.response.body':
            if fail_send:
                raise RuntimeError('synthetic send failure')
            chunks.append(message.get('body', b''))

    async def run():
        await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)

    if fail_send:
        with pytest.raises(RuntimeError, match='synthetic send failure'):
            asyncio.run(run())
    else:
        asyncio.run(run())
        assert b''.join(chunks) == content
    assert file.closed
