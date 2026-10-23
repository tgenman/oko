from pathlib import Path


def channel_sdk(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Keep the installed SDK types/requests; replace only the Telegram network client."""
    project = tmp_path / 'sdk'
    project.mkdir()
    (project / 'fixture.session').touch()
    env = project / '.env'
    env.write_text('TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=fixture\nTELEGRAM_SESSION_NAME_FIXTURE=fixture.session\n')
    trace = project / 'calls.jsonl'
    monkeypatch.setenv('SDK_CHANNEL_TRACE', str(trace))
    import os
    monkeypatch.setenv('PYTHONPATH', str(project) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    (project / 'sitecustomize.py').write_text('''
import json, os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import telethon
from telethon import types

now = datetime.now(timezone.utc)
class FakeClient:
    def __init__(self, session, *args, **kwargs):
        self.session = SimpleNamespace(filename=session)
        self.entity = types.Channel(id=1234567890, title='Channel', photo=types.ChatPhotoEmpty(),
                                   date=now, broadcast=True, megagroup=False, forum=False, username='forum', access_hash=1)
    async def connect(self): pass
    async def disconnect(self): pass
    async def get_me(self):
        identity = int(Path(os.environ['SDK_IDENTITY_FILE']).read_text()) if os.environ.get('SDK_IDENTITY_FILE') else 10001
        self.log(op='identity', id=identity)
        return type('User', (), {'id': identity})()
    async def is_user_authorized(self): return True
    async def get_entity(self, peer): return self.entity
    def log(self, **body):
        with Path(os.environ['SDK_CHANNEL_TRACE']).open('a') as f: f.write(json.dumps(body) + '\\n')
    async def __call__(self, request):
        name = type(request).__name__
        self.log(op=name, topics=getattr(request, 'topics', None),
                 topic=getattr(request, 'msg_id', None), maximum=getattr(request, 'read_max_id', None))
        if name == 'GetPeerDialogsRequest':
            return SimpleNamespace(dialogs=[SimpleNamespace(read_inbox_max_id=0)])
        raise RuntimeError('unexpected broad request')
    def rows(self):
        def message(id, topic, group=None, photo=False):
            return SimpleNamespace(id=id, date=now, message='Topic '+str(topic), grouped_id=group,
                photo=photo, document=None, media=photo, file=SimpleNamespace(size=10), action=None,
                reply_to=None if topic == 1 else SimpleNamespace(reply_to_top_id=topic, reply_to_msg_id=topic, forum_topic=True))
        # A full first page in other topics must not stall General's cursor.
        rows = [message(i,30) for i in range(31,131)]
        rows += [message(131,1), message(140,20,777,True), message(141,20,777,True), message(142,30)]
        return rows
    async def get_messages(self, entity, **params):
        self.log(op='history', reply_to=params.get('reply_to'), after=params.get('offset_id'), ids=params.get('ids'))
        rows = self.rows()
        if 'ids' in params:
            if isinstance(params['ids'], int): return next((m for m in rows if m.id == params['ids']), None)
            return [m for m in rows if m.id in params['ids']]
        if params.get('reply_to'):
            rows = [m for m in rows if m.reply_to and m.reply_to.reply_to_top_id == params['reply_to']]
        if params.get('max_id'): rows = [m for m in rows if m.id < params['max_id']]
        after = params.get('offset_id') or 0
        if params.get('reverse'):
            rows = [m for m in rows if m.id > after]
        else:
            rows = sorted([m for m in rows if not after or m.id < after], key=lambda m:-m.id)
        return rows[:params.get('limit',100)]
    async def download_media(self, message, file, progress_callback):
        self.log(op='download', id=message.id)
        file.write(b'fixture-image')
        return True
    async def send_read_acknowledge(self, *args, **kwargs):
        self.log(op='ACK',maximum=kwargs.get('max_id'))
        return True

telethon.TelegramClient = FakeClient
''')
    return env, trace
