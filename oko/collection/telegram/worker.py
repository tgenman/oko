"""Private JSON pipe to the Oko-owned Telethon client.

This worker only resolves peers, reads history and downloads originals with
Telethon. It does not perform transcription or AI processing.
"""
import asyncio
import base64
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
from oko.diagnostics import exception_details
import logging
from urllib.parse import urlsplit
import os
from pathlib import Path
import sys
from typing import Any

from oko.collection.telegram.topics import history, message_topic, topic_info, read_state, read_window

MAX_MEDIA = 200 * 1024 * 1024


class ChangedMedia(Exception):
    pass


def message_text(message: Any) -> str:
    raw = message.message or ''
    encoded = raw.encode('utf-16-le')
    links = []
    fallback = []
    for entity in (getattr(message,'entities',None) or []):
        url = getattr(entity,'url',None)
        if not isinstance(url,str):
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http','https') or not parsed.hostname:
                continue
        except ValueError:
            continue
        start,end = entity.offset*2,(entity.offset+entity.length)*2
        if not 0<=start<end<=len(encoded):
            fallback.append(url)
            continue
        links.append((start,end,url))
    output = []
    offset = 0
    for start,end,url in sorted(links):
        if start<offset:
            fallback.append(url)
            continue
        try:
            prefix = encoded[offset:start].decode('utf-16-le')
            label = encoded[start:end].decode('utf-16-le')
        except UnicodeError:
            fallback.append(url)
            continue
        output.append(prefix+'['+label+']('+url.replace('(','%28').replace(')','%29')+')')
        offset = end
    output.append(encoded[offset:].decode('utf-16-le'))
    return ''.join(output)+''.join('\n\nСсылка: '+url for url in dict.fromkeys(fallback))


def record(msg: Any) -> dict[str, Any]:
    if getattr(msg, 'action', None) is not None:
        return dict(id=msg.id, date=msg.date.timestamp(), text='', group=None, media=None, service=True)
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
    if media is not None:
        media['name']=getattr(getattr(msg,'file',None),'name',None) or ''
        original=msg.photo or msg.document
        identity=getattr(original,'id',None)
        if identity is not None:
            media['identity']=('photo:' if msg.photo else 'document:')+str(identity)
        size=getattr(getattr(msg,'file',None),'size',None)
        if size is not None:
            media['size']=size
    edited = getattr(msg,'edit_date',None)
    return dict(id=msg.id, date=msg.date.timestamp(), text=message_text(msg), original_text=msg.message or '',
                group=msg.grouped_id, media=media, service=False,
                edited=edited.timestamp() if edited else None)


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
    avatars: dict[tuple[str, int], tuple[int | None, str | None]] = {}

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

    async def entity_for(cl: Any, account: str, peer: str | int, *, refresh: bool = False) -> Any:
        cache_key = (account, str(peer))
        if refresh or cache_key not in peers or isinstance(peer, str):
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
                    if op == 'identity':
                        user = await cl.get_me()
                        output.write(json.dumps(dict(value=int(user.id))) + '\n')
                        output.flush()
                        continue
                    if 'expected_user_id' in body and int((await cl.get_me()).id) != body['expected_user_id']:
                        raise PermissionError('account-identity-mismatch')
                    entity = await entity_for(cl, body['account'], body['peer'], refresh=op == 'info')
                    topic_id = body.get('topic_id') if op in ('read_state', 'read_window', 'acknowledge') else None
                    if topic_id is not None and not getattr(entity, 'forum', False):
                        raise ValueError('topic requires forum')
                    if op == 'info':
                        value = dict(peer=utils.get_peer_id(entity), title=entity.title,
                                     kind='channel' if getattr(entity, 'broadcast', False) else 'group', forum=bool(getattr(entity, 'forum', False)), username=getattr(entity, 'username', None),
                                     usernames=[u.username for u in (getattr(entity, 'usernames', None) or []) if u.active])
                        photo_id = getattr(getattr(entity, 'photo', None), 'photo_id', None)
                        key = (body['account'], utils.get_peer_id(entity))
                        try:
                            if key not in avatars or avatars[key][0] != photo_id:
                                photo = await cl.download_profile_photo(entity, file=bytes, download_big=False) if photo_id else None
                                if photo is not None and (not isinstance(photo, bytes) or len(photo) > 128*1024):
                                    raise ValueError('avatar size')
                                avatars[key] = (photo_id, base64.b64encode(photo).decode('ascii') if photo else None)
                            value['avatar'] = avatars[key][1]
                        except FloodWaitError:
                            raise
                        except Exception:
                            pass  # Optional artwork must not stop collection or erase a cached avatar.
                    elif op == 'history_page':
                        upper, before = body['upper'], body['before']
                        first = not upper
                        archive_params: dict[str, Any] = dict(limit=100)
                        if before:
                            archive_params['offset_id'] = before
                        elif upper:
                            archive_params['offset_id'] = upper + 1
                        raw, batch = await history(cl, entity, topic_id, **archive_params)
                        upper = upper or max([0] + [m.id for m in raw])
                        expanded = {m.id: m for m in batch}
                        for msg in (batch[0:1] + batch[-1:]) if batch else []:
                            if msg.grouped_id:
                                expanded.update({m.id: m for m in await album(cl, entity, msg, topic_id)})
                        if first:
                            upper = max([upper] + list(expanded))
                        value = dict(messages=[record(m) for m in expanded.values()], upper=upper,
                                     next_before=min([m.id for m in raw], default=before), more=len(raw) == 100)
                    elif op == 'page':
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
                        elif getattr(entity, 'forum', False):
                            before = await cl.get_messages(entity, limit=1, offset_date=since)
                            value = dict(read_max=0, initial=before[0].id if before else 0, forum=True)
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
                            value = dict(ids=sorted(m.id for m in batch), more=len(batch) == 100)
                            if getattr(entity, 'forum', False):
                                value['topics'] = {str(m.id): message_topic(m) for m in batch}
                                boundaries = {}
                                for topic in set(value['topics'].values()):
                                    try:
                                        boundaries[topic] = (await topic_info(cl, entity, topic)).read_inbox_max_id
                                    except ValueError:
                                        # Deleted/unknown topic: keep it unread and let its
                                        # explicit acknowledgement surface the problem.
                                        boundaries[topic] = 0
                                value['read_ids'] = [m.id for m in batch if m.id <= boundaries[message_topic(m)]]
                    elif op == 'acknowledge':
                        max_id = body['max_id']
                        if not isinstance(max_id, int) or max_id <= 0:
                            raise ValueError('read boundary')
                        if topic_id is not None:
                            from telethon import functions
                            await topic_info(cl, entity, topic_id)
                            value = bool(await cl(functions.messages.ReadDiscussionRequest(
                                peer=entity, msg_id=topic_id, read_max_id=max_id)))
                        elif getattr(entity, 'forum', False):
                            raise ValueError('forum requires a verified topic boundary')
                        else:
                            value = bool(await cl.send_read_acknowledge(entity, max_id=max_id))
                    elif op == 'download':
                        msg = await cl.get_messages(entity, ids=body['id'])
                        if not msg or not msg.media or (topic_id is not None and message_topic(msg) != topic_id):
                            raise FileNotFoundError('media')
                        expected=body.get('expected_media_identity')
                        if expected is not None and (record(msg)['media'] or {}).get('identity')!=expected:
                            raise ChangedMedia()
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
            except SessionLockError as error:
                response = dict(error='Telegram-сессия занята другим клиентом', **exception_details(error))
            except ChangedMedia as error:
                response=dict(error='Вложение Telegram изменилось',**exception_details(error))
            except FloodWaitError as error:
                response = dict(error='Ограничение частоты Telegram', delay=max(1, error.seconds), **exception_details(error))
            except PermissionError as error:
                response = dict(error='Требуется авторизация существующего Telegram-клиента', **exception_details(error))
            except OverflowError as error:
                response = dict(error='Вложение Telegram превышает лимит 200 МБ', **exception_details(error))
            except Exception as error:
                label = 'Нет доступа к Telegram-источнику' if type(error).__name__ in (
                    'ChannelPrivateError', 'ChatAdminRequiredError', 'UsernameInvalidError',
                    'UsernameNotOccupiedError', 'PeerIdInvalidError', 'ValueError') else 'Telegram недоступен'
                response = dict(error=label, **exception_details(error))
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
