"""Targeted projection of Telegram read-Job problems.

This module does not mutate the store. The event owner must persist the result
within its own write transaction, including group suppression and recovery.
"""
from typing import Any, Literal

def classify_legacy(identity: str, body: Any) -> Literal['read','other','ambiguous']:
    """Classify old untyped Job keys without guessing when text IDs overlap."""
    if not identity.startswith('job:'):
        return 'other'
    if not isinstance(body,dict):
        return 'ambiguous'
    operation=body.get('operation')
    if operation=='producer:read':
        return 'read'
    if operation not in (None,'producer:status'):
        return 'other'
    code=body.get('code')
    if not isinstance(code,str):
        return 'ambiguous' if ':read' in identity else 'other'
    prefix='job:'+code+':'
    if not identity.startswith(prefix):
        return 'ambiguous' if ':read' in identity else 'other'
    tail=identity[len(prefix):]
    kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
    if code.startswith('source-'):
        found=[kind for kind in kinds if tail.endswith(':'+kind) and len(tail)>len(kind)+1]
    else:
        found=[kind for kind in kinds if ':'+kind+':' in tail]
    if len(found)==1:
        return 'read' if found[0]=='read' else 'other'
    return 'ambiguous' if 'read' in tail else 'other'
