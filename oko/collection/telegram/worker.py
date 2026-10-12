"""Private JSON pipe to the Oko-owned Telethon client.

This worker only resolves peers, reads history and downloads originals with
Telethon. It does not perform transcription or AI processing.
"""
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

from oko.collection.telegram.topics import history, list_topics, message_topic, topic_info, read_state, read_window

MAX_MEDIA = 200 * 1024 * 1024


def record(msg: Any) -> dict[str, Any]:
    media = None
    if msg.photo:
        media = dict(kind='image', mime='image/jpeg', extension='jpg')
    elif msg.document:
        mime = msg.document.mime_type or 'application/octet-stream'
        kind = 'circle' if msg.video_note else 'video' if msg.video else 'audio' if msg.audio or msg.voice else 'file'
        supported = {'image/jpeg': ('image', 'jpg'), 'image/png': ('image', 'png'),
                     'image/webp': ('image', 'webp'), 'image/gif': ('image', 'gif'),
                     'video/mp4': (kind, 'mp4'), 'video/webm': ('video', 'webm'),
                     'audio/ogg': ('audio', 'ogg'), 'audio/mpeg': ('audio', 'mp3'),
                     'application/pdf': ('file', 'pdf')}
        kind, extension = supported.get(mime, ('file', 'bin'))
        media = dict(kind=kind, mime=mime if extension != 'bin' else 'application/octet-stream', extension=extension)
    elif msg.media and type(msg.media).__name__ != 'MessageMediaWebPage':
        media = dict(kind='unsupported', mime='application/octet-stream', extension='bin')
    return dict(id=msg.id, date=msg.date.timestamp(), text=msg.message or '',
                group=msg.grouped_id, media=media)


async def main() -> None:
    # Read the explicit existing .env without importing the external project. stdout
    # is reserved for the pipe; neither SDK errors nor content are logged.
    output = sys.stdout
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    try:
        with open(os.devnull, 'w') as sink, redirect_stdout(sink):
            from oko.collection.telegram.client import ClientRegistry, SessionLock, SessionLockError, session_identity
            registry = ClientRegistry(Path(sys.argv[2]))
            from telethon import types, utils  # type: ignore[import-untyped]
            from telethon.errors import FloodWaitError  # type: ignore[import-untyped]
    except BaseException:
        output.write(json.dumps(dict(error='Окружение Telegram-клиента недоступно')) + '\n')
        output.flush()
        return
    spool = Path(sys.argv[1])
    locks: dict[str, Any] = {}
    peers: dict[tuple[str, str], Any] = {}

    async def client_for(account: str) -> Any:
        cl = registry.client(account)
        if account not in locks:
            lock = SessionLock(session_identity(cl))
            lock.acquire()
            locks[account] = lock
            try:
                await cl.connect()
                if not await cl.is_user_authorized():
                    raise PermissionError('authorization')
            except BaseException:
                await cl.disconnect()
                locks.pop(account).release()
                raise
        return cl

    async def entity_for(cl: Any, account: str, peer: str | int) -> Any:
        cache_key = (account, str(peer))
        if cache_key not in peers:
            # Numeric private peers require an existing access_hash. Populate the
            # client's cache once, without exporting dialogs into Oko.
            try:
                entity = await cl.get_entity(peer)
            except ValueError:
                if not isinstance(peer, int):
                    raise
                await cl.get_dialogs(limit=None)
                entity = await cl.get_entity(peer)
            if not isinstance(entity, (types.Channel, types.Chat)):
                raise ValueError('peer type')
            peers[cache_key] = entity
            peers[(account, str(utils.get_peer_id(entity)))] = entity
        return peers[cache_key]

    async def album(cl: Any, entity: Any, msg: Any, topic_id: int | None) -> list[Any]:
        if not msg.grouped_id:
            return [msg]
        # Scan history, not numeric neighbours: IDs in basic groups may have gaps
        # from other dialogs. Telegram albums contain at most ten messages.
        before = await cl.get_messages(entity, limit=10, offset_id=msg.id)
        after = await cl.get_messages(entity, limit=10, offset_id=msg.id, reverse=True)
        return sorted({m.id: m for m in [*before, msg, *after]
                       if m and m.grouped_id == msg.grouped_id and (topic_id is None or message_topic(m) == topic_id)}.values(), key=lambda m: m.id)

    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            try:
                body = json.loads(line)
                op = body['op']
                if op == 'accounts':
                    value: Any = registry.accounts()
                else:
                    cl = await client_for(body['account'])
                    entity = await entity_for(cl, body['account'], body['peer'])
                    topic_id = body.get('topic_id')
                    if getattr(entity, 'forum', False) and topic_id is None and op not in ('info', 'topics'):
                        raise ValueError('choose forum topic')
                    if topic_id is not None and not getattr(entity, 'forum', False):
                        raise ValueError('topic requires forum')
                    if op == 'topics':
                        value = await list_topics(cl, entity)
                    elif op == 'info':
                        topic = await topic_info(cl, entity, topic_id) if topic_id is not None else None
                        value = dict(peer=utils.get_peer_id(entity), title=entity.title + (' · ' + topic.title if topic else ''),
                                     kind='channel' if getattr(entity, 'broadcast', False) else 'group', forum=bool(getattr(entity, 'forum', False)), topic_id=topic_id, username=getattr(entity, 'username', None))
                    elif op == 'page':
                        if topic_id is not None:
                            await topic_info(cl, entity, topic_id)
                        after, since = body['after'], body['since']
                        params: dict[str, Any] = dict(limit=100, reverse=True)
                        if after:
                            params['offset_id'] = after
                        else:
                            params['offset_date'] = datetime.fromtimestamp(since, timezone.utc)
                        raw, batch = await history(cl, entity, topic_id, **params)
                        expanded = {m.id: m for m in batch}
                        for msg in (batch[0:1] + batch[-1:]) if batch else []:
                            if msg.grouped_id:
                                expanded.update({m.id: m for m in await album(cl, entity, msg, topic_id)})
                        messages = [record(m) for m in sorted(expanded.values(), key=lambda m: m.id)]
                        gap = None
                        if after:
                            anchor = await cl.get_messages(entity, ids=after)
                            if not anchor:
                                gap = 'Сохранённая граница удалена или недоступна; история могла иметь пробел'
                        value = dict(messages=messages, more=len(raw) == 100, gap=gap, next_after=max([after] + [m.id for m in raw] + list(expanded)))
                    elif op == 'messages':
                        batch = await cl.get_messages(entity, ids=body['ids'])
                        batch = [m for m in batch if m and (topic_id is None or message_topic(m) == topic_id)]
                        expanded = {m.id: m for m in batch}
                        for msg in batch:
                            if msg and msg.grouped_id:
                                expanded.update({m.id: m for m in await album(cl, entity, msg, topic_id)})
                        value = [record(m) for m in sorted(expanded.values(), key=lambda m: m.id)]
                    elif op == 'read_state':
                        since = datetime.fromtimestamp(body['since'], timezone.utc)
                        if topic_id is not None:
                            value = await read_state(cl, entity, topic_id, since)
                        else:
                            from telethon import functions
                            dialog = await cl(functions.messages.GetPeerDialogsRequest(
                                peers=[types.InputDialogPeer(peer=await cl.get_input_entity(entity))]))
                            if not dialog.dialogs:
                                raise ValueError('read state')
                            before = await cl.get_messages(entity, limit=1, offset_date=since)
                            value = dict(read_max=dialog.dialogs[0].read_inbox_max_id,
                                         initial=before[0].id if before else 0)
                    elif op == 'read_window':
                        if topic_id is not None:
                            value = await read_window(cl, entity, topic_id, body['after'], body['until'])
                        else:
                            batch = await cl.get_messages(entity, limit=100, reverse=True,
                                offset_id=body['after'], max_id=body['until']+1)
                            value = dict(ids=[m.id for m in batch], more=len(batch) == 100)
                    elif op == 'acknowledge':
                        max_id = body['max_id']
                        if not isinstance(max_id, int) or max_id <= 0:
                            raise ValueError('read boundary')
                        if topic_id is not None:
                            from telethon import functions
                            await topic_info(cl, entity, topic_id)
                            value = bool(await cl(functions.messages.ReadDiscussionRequest(
                                peer=entity, msg_id=topic_id, read_max_id=max_id)))
                        else:
                            value = bool(await cl.send_read_acknowledge(entity, max_id=max_id))
                    elif op == 'download':
                        msg = await cl.get_messages(entity, ids=body['id'])
                        if not msg or not msg.media or (topic_id is not None and message_topic(msg) != topic_id):
                            raise FileNotFoundError('media')
                        if msg.file and msg.file.size and msg.file.size > MAX_MEDIA:
                            raise OverflowError('media')
                        path = spool / 'media.bin'
                        def progress(current: int, total: int) -> None:
                            if current > MAX_MEDIA or total > MAX_MEDIA:
                                raise OverflowError('media')
                        try:
                            with path.open('wb') as target:
                                downloaded = await cl.download_media(msg, file=target, progress_callback=progress)
                            if not downloaded or not path.stat().st_size:
                                raise FileNotFoundError('media')
                        except BaseException:
                            path.unlink(missing_ok=True)
                            raise
                        value = True
                    else:
                        raise ValueError('operation')
                response = dict(value=value)
            except SessionLockError:
                response = dict(error='Telegram-сессия занята другим клиентом')
            except FloodWaitError as error:
                response = dict(error='Ограничение частоты Telegram', delay=max(1, error.seconds))
            except PermissionError:
                response = dict(error='Требуется авторизация существующего Telegram-клиента')
            except OverflowError:
                response = dict(error='Вложение Telegram превышает лимит 200 МБ')
            except Exception as error:
                label = 'Нет доступа к Telegram-источнику' if type(error).__name__ in (
                    'ChannelPrivateError', 'ChatAdminRequiredError', 'UsernameInvalidError',
                    'UsernameNotOccupiedError', 'PeerIdInvalidError', 'ValueError') else 'Telegram недоступен'
                response = dict(error=label)
            output.write(json.dumps(response, ensure_ascii=False) + '\n')
            output.flush()
    finally:
        try:
            await registry.close()
        finally:
            for lock in locks.values():
                lock.release()
        (spool / 'media.bin').unlink(missing_ok=True)


if __name__ == '__main__':
    asyncio.run(main())
