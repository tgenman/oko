"""Merge only adapter-proven legacy identities on the offline working copy."""
from collections import Counter
import hashlib
import shutil
from typing import Any,TYPE_CHECKING
from oko.collection.adapters import ExternalReference
from oko.collection.contracts import Material,StagedPayload

if TYPE_CHECKING:
    from oko.storage.store import Store


def _files(material: Material, youtube: bool) -> list[str]:
    files={image.name:image.data for image in material.images}
    for attachment in material.attachments:
        if youtube and attachment.mime=='text/plain':
            continue
        value=attachment.data
        files[attachment.name]=value.path.read_bytes() if isinstance(value,StagedPayload) else value
    return sorted(hashlib.sha256(value).hexdigest() for value in files.values())


def merge(store: 'Store', item: str, reference: ExternalReference, report: dict[str,Any]) -> bool:
    other=store.db.execute('SELECT id FROM items WHERE platform=? AND external_id=? AND id!=?',
        (reference.key.platform.value,reference.key.external_id,item)).fetchone()
    if other is None:
        return False
    source=store.db.execute('SELECT * FROM items WHERE id=?',(item,)).fetchone()
    old,new=store.publication(item),store.publication(other[0])
    if not old or not new or not old.teaser or not new.teaser:
        raise ValueError('Shared legacy copies require compatible readable latest states')
    def value(material: Material) -> tuple[Any,...]:
        return (material.title,material.text,material.description,
                material.source_manifest.post if material.source_manifest else None)
    youtube=reference.key.platform.value=='youtube'
    if value(old.teaser)!=value(new.teaser) or not Counter(_files(old.teaser,youtube))<=Counter(_files(new.teaser,youtube)):
        raise ValueError('Conflicting latest states for the same external publication key')
    if reference.key.platform.value=='telegram' and old.teaser.source_manifest and old.teaser.source_manifest.source_url not in (None,reference.url):
        raise ValueError('Legacy Telegram manifest contradicts its external identity')
    target=other[0]
    if any((store.path(item).parent/name).is_file() for name in ('filter.md','theses.md')):
        report['unportable'].append(dict(publication_id=item,reason='Merged duplicate: retained selected publication results; alternate results remain in backup'))
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO feed_publications VALUES (?,?)',(source['feed_id'],target))
        store.db.execute('INSERT OR IGNORE INTO history_members SELECT feed,?,outcome FROM history_members WHERE item=?',(target,item))
        store.db.execute('DELETE FROM history_members WHERE item=?',(item,))
        store.filters.forget(item)
        store.versions.forget(item)
        store.youtube.reset(item)
        for table in ('thesis_inputs','retained_theses','processing_dirty'):
            store.db.execute('DELETE FROM '+table+' WHERE item=?',(item,))
        store.db.execute('DELETE FROM execution_settings WHERE job IN (SELECT id FROM jobs WHERE item=?)',(item,))
        store.db.execute('DELETE FROM jobs WHERE item=?',(item,))
        store.db.execute('DELETE FROM items WHERE id=?',(item,))
    for kind in ('content','staging','media'):
        path=store.root/kind/item
        if path.exists():
            shutil.rmtree(path)
    report['publications'].append(dict(old_id=item,new_id=target,old_key=source['source_id'],
        platform=reference.key.platform.value,external_id=reference.key.external_id,results=[],merged=True))
    return True
