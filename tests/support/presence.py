"""Legacy scenario checkpoints; browser presence no longer controls execution.

Kept while unrelated scenario setups migrate to explicit status observations.
Lifecycle tests exercise retired session endpoints directly.
"""
from httpx import Client, Response


def presence(client: Client, name: str = 'reader') -> Response:
    return client.get('/api/v3/status')


def close_presence(client: Client, name: str = 'reader') -> Response:
    return client.delete('/api/v3/client-sessions/' + name)
