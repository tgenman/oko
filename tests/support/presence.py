"""A synthetic client owns opaque presence sessions, just like a real client."""
from weakref import WeakKeyDictionary
from httpx import Client, Response

_sessions: WeakKeyDictionary[Client, dict[str, str]] = WeakKeyDictionary()
_closed: WeakKeyDictionary[Client, dict[str, str]] = WeakKeyDictionary()


def presence(client: Client, name: str = 'reader') -> Response:
    sessions = _sessions.setdefault(client, {})
    if name in sessions:
        return client.put('/api/v3/client-sessions/' + sessions[name])
    reply = client.post('/api/v3/client-sessions')
    if reply.status_code == 201:
        sessions[name] = reply.json()['id']
    return reply


def close_presence(client: Client, name: str = 'reader') -> Response:
    sessions=_sessions.get(client,{})
    identity=sessions.pop(name,None) or _closed.get(client,{}).get(name,'absent')
    _closed.setdefault(client,{})[name]=identity
    return client.delete('/api/v3/client-sessions/' + identity)


def new_presence(client: Client, name: str = 'reader') -> Response:
    _sessions.setdefault(client,{}).pop(name,None)
    return presence(client,name)
