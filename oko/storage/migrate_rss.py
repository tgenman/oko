"""Preserve the cached legacy RSS document without inventing a lost excerpt."""
from dataclasses import replace
from datetime import datetime,timezone
import json
import os
import shutil
import sqlite3
from typing import Any,TYPE_CHECKING

from oko.collection.adapters import PublicationContent,PublicationMetadata,MediaReference,AdapterException
from oko.collection.contracts import ArticleManifest,SourceManifest,ResourceManifest,SourceError
from oko.collection.rss_adapter import publication_reference,legacy_cursor
from oko.collection.rss import normalized
from oko.storage.migrate_articles import split

if TYPE_CHECKING:
    from oko.storage.store import Store


def convert(store: 'Store', report: dict[str,Any]) -> None:
    for feed in store.subscriptions.feeds():
        if (feed['transport'] or feed['kind'])!='rss' or feed['connection']=='deleting' or store.publications.feed(feed['id']):
            continue
        try:
            _feed(store,feed,report)
        except (ValueError,sqlite3.IntegrityError,AdapterException,SourceError) as error:
            report['conflicts'].append(dict(feed_id=feed['id'],reason=str(error)))


def _feed(store: 'Store', feed: dict[str,Any], report: dict[str,Any]) -> None:
    url=normalized(feed['original_urls'][0])
    identities=[]
    with store.db:
        store.publications.configure_feed(feed['id'],migrate_legacy=True)
    for row in store.db.execute('SELECT * FROM items WHERE feed_id=? AND viewed=0 AND platform IS NULL',(feed['id'],)).fetchall():
        item=row['id'];identity=row['source_id'].split(':',1)[1]
        ref=publication_reference(url,identity,row['url']);identities.append(identity)
        publication=store.publication(item)
        material=publication.teaser if publication else None
        with store.db:
            store.publications.identify(item,ref)
        if material:
            directory=store.path(item).parent
            header,_=store.original(item)
            names={value['name']:value for value in header.get('attachments',[])}
            for name in [image.name for image in material.images]:
                names.setdefault(name,dict(name=name,kind='image',mime='image/'+name.rsplit('.',1)[-1]))
            resources=tuple(ResourceManifest(key='legacy-rss:'+name,origin='legacy:'+name,order=n,name=name,
                kind=value['kind'],mime=value['mime'],status='loaded',sha256=store.versions.file_digest(directory/name))
                for n,(name,value) in enumerate(names.items()) if (directory/name).is_file())
            cached_resources=resources
            if material.full and row['url'] and row['url']!=url:
                manifest=SourceManifest(post=dict(text=''),resources=resources,articles=(ArticleManifest(
                    url=row['url'],title=material.title,text=material.text,resource_keys=tuple(r.key for r in resources)),))
                material=replace(split(store,item,replace(material,source_manifest=manifest),directory,report),images=())
                cached_resources=()
                report['unportable'].append(dict(publication_id=item,reason='Original RSS excerpt was not retained; cached article preserved, RSS metadata refresh scheduled'))
            else:
                report['unportable'].append(dict(publication_id=item,reason='Ambiguous legacy partial RSS document retained until source refresh'))
            from oko.collection.web_adapter import reference as article_reference
            value=PublicationContent(PublicationMetadata(ref,material.title,
                datetime.fromtimestamp(row['published'],timezone.utc) if row['published_known'] else None),
                dict(blocks=([dict(type='text',role='body',text=material.text)] if material.text else [])+
                    [dict(type='media',role='attachment' if r.kind=='file' else r.kind,ref={'$media':r.key}) for r in cached_resources],extra={}),
                media=tuple(MediaReference(r.key,'','',r.mime) for r in cached_resources),
                linked_publications=(article_reference(row['url']),) if row['url'] and row['url']!=url else (),needs_fetch=True)
            material=replace(material,adapter_content=value)
            version=store.versions.prepare(item,material)
            prepared=store.publications.prepare(item,material,store.versions.path(item,version[0]).parent)
            for media,resource in zip(prepared[1],cached_resources):
                key='media/'+item+'/'+media['id']
                target=store.root/key;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(directory/resource.name,target)
                with target.open('rb') as stream:
                    os.fsync(stream.fileno())
                media.update(storage_key=key,size_bytes=target.stat().st_size,status='ready',reason='')
            store.media.project_header(prepared[0],prepared[1])
            with store.db:
                store.versions.publish(item,version,False)
                store.publications.publish(item,prepared,version[1])
                store.db.execute('UPDATE items SET complete=1 WHERE id=?',(item,))
            if any((directory/name).is_file() for name in ('filter.md','theses.md')):
                report['unportable'].append(dict(publication_id=item,reason='RSS/article attribution changed; prior results remain in backup and require reprocessing'))
        with store.db:
            store.enqueue_fetch(item)
        report['publications'].append(dict(old_id=item,new_id=item,old_key=row['source_id'],platform='rss',external_id=ref.key.external_id,results=[]))
    history=feed['initial_history'] or {}
    state=dict(rss_adapter=legacy_cursor(url,identities),run=history.get('run','initial'),initial_complete=True,
               locating=False,scan_complete=True,history_enroll=[])
    with store.db:
        store.db.execute('UPDATE feeds SET cursor=? WHERE id=?',(json.dumps(state),feed['id']))
        store.db.execute('DELETE FROM state WHERE key=?',(store.cursor_key(feed['id'],''),))
    report['feeds'].append(dict(old_id=feed['id'],new_id=feed['id'],old_key=feed['identity'],platform='rss',external_id=url))
