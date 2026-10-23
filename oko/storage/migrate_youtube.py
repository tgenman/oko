"""Offline conversion of the last readable YouTube state; no provider calls."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
from typing import Any,TYPE_CHECKING

from oko.collection.adapters import AdapterException,MediaReference,PublicationContent,PublicationMetadata
from oko.collection.youtube.adapter import video_reference,feed_reference,legacy_cursor
from oko.reviews import publication_input
from oko.storage.reading import saved_document

if TYPE_CHECKING:
    from oko.storage.store import Store


def convert(store: 'Store', report: dict[str,Any]) -> None:
    for feed in store.subscriptions.feeds():
        if feed['transport']!='youtube' or feed['connection']=='deleting':
            continue
        if store.publications.feed(feed['id']):
            continue
        legacy=json.loads(store.cursor(feed['id']) or '{}')
        selection=store.db.execute('SELECT selection FROM initial_history WHERE feed=?',(feed['id'],)).fetchone()
        selection=json.loads(selection[0]) if selection else {}
        for row in store.db.execute('SELECT * FROM items WHERE feed_id=? AND platform IS NULL',(feed['id'],)).fetchall():
            item=row['id']
            publication=store.publication(item)
            material=publication.teaser if publication else None
            results={}
            if material:
                if store.filters.read(item)['status']=='done':
                    results['filter']=dict(file=(store.path(item).parent/'filter.md').read_text())
                if store.processing.theses.read(item) is not None:
                    accepted=store.processing.theses._accepted(item)
                    assert accepted
                    results['theses']=dict(file=accepted[1].read_text())
            try:
                reference=video_reference(row['source_id'].split(':',1)[1])
                from oko.storage.migrate_shared import merge
                if merge(store,item,reference,report):
                    continue
                with store.db:
                    store.publications.identify(item,reference)
            except (sqlite3.IntegrityError,ValueError,AdapterException) as error:
                report['conflicts'].append(dict(publication_id=item,reason=str(error)))
                continue
            mapping=dict(old_id=item,new_id=item,old_key=row['source_id'],platform='youtube',external_id=reference.key.external_id,results=[])
            if material:
                directory=store.path(item).parent
                header,_=saved_document(directory/'original.md')
                state=material.captions or 'not_requested'
                metadata=PublicationMetadata(reference,material.title,datetime.fromtimestamp(row['published'],timezone.utc) if row['published_known'] else None)
                refs=(MediaReference('youtube:cover','https://i.ytimg.com/vi/'+reference.key.external_id+'/hqdefault.jpg','',mime_type='image/jpeg'),
                      MediaReference('youtube:subtitles','youtube:'+reference.key.external_id+':subtitles','',mime_type='text/plain'))
                content=dict(blocks=[dict(type='external_media',kind='youtube',url=reference.url),
                    *[dict(type='media',role=role,ref={'$media':ref.key}) for role,ref in zip(('cover','subtitles'),refs)]],
                    extra=dict(youtube=dict(captions=state,description=material.description)))
                from dataclasses import replace
                material=replace(material,adapter_content=PublicationContent(metadata,content,refs))
                prepared=store.publications._prepare_adapter(item,material,header,directory)
                for media in prepared[1]:
                    captions=media['resource_key']=='youtube:subtitles'
                    raw=(material.text.encode('utf-8') if state=='available' else None) if captions else material.images[0].data if material.images else None
                    if raw is not None:
                        key='media/'+item+'/'+media['id']
                        path=store.root/key
                        path.parent.mkdir(parents=True,exist_ok=True)
                        store.versions._write(path,raw)
                        media.update(storage_key=key,size_bytes=len(raw),status='ready',reason='')
                        if captions:
                            prepared[0]['extra']['youtube']['captions_digest']=hashlib.sha256(raw).hexdigest()
                    elif captions and state in ('unavailable','not_requested'):
                        media.update(status='unavailable',reason='Субтитры недоступны' if state=='unavailable' else 'Субтитры не запрошены')
                with store.db:
                    store.publications.publish(item,prepared,store.content_version(item))
                    store.media.project_header(prepared[0],prepared[1])
                    store.db.execute('UPDATE items SET content=? WHERE id=?',(json.dumps(prepared[0],ensure_ascii=False),item))
                    for kind,value in results.items():
                        header_text,text=value['file'].split('\n\n',1)
                        saved=json.loads(header_text)
                        saved['signature']=publication_input.signature(store,item,kind)
                        value['file']=json.dumps(saved,ensure_ascii=False)+'\n\n'+text
                        store.publications.results(item,{kind:value})
                        table='filter_inputs' if kind=='filter' else 'thesis_inputs'
                        store.db.execute('UPDATE '+table+' SET signature=? WHERE item=?',(saved['signature'],item))
                    store.publications.status(item)
                mapping['results']=list(results)
            elif not row['viewed']:
                report['unportable'].append(dict(publication_id=item,reason='No readable latest content; fetch scheduled'))
                with store.db:
                    store.db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1",(item,))
            report['publications'].append(mapping)
        try:
            with store.db:
                store.publications.configure_feed(feed['id'])
            current=store.db.execute('SELECT platform,external_id FROM feeds WHERE id=?',(feed['id'],)).fetchone()
            history=legacy.get('youtube_history',{})
            chosen=[row[0] for row in store.db.execute('SELECT i.external_id FROM items i JOIN feed_publications f ON f.publication_id=i.id WHERE f.feed_id=? AND i.platform=\'youtube\' ORDER BY i.published,i.id',(feed['id'],))]
            position_cursor=legacy_cursor(feed_reference(feed['original_urls'][0]),legacy,selection,chosen)
            complete=not history or history.get('complete',False)
            cursor=dict(youtube_adapter=position_cursor,run=selection.get('run','initial'),
                count=selection.get('count') or 1,locating=False,initial_complete=complete,scan_complete=complete)
            with store.db:
                store.db.execute('UPDATE feeds SET cursor=? WHERE id=?',(json.dumps(cursor),feed['id']))
                store.db.execute('DELETE FROM state WHERE key=?',(store.cursor_key(feed['id'],''),))
            report['feeds'].append(dict(old_id=feed['id'],new_id=feed['id'],old_key=feed['identity'],platform=current[0],external_id=current[1]))
        except (sqlite3.IntegrityError,ValueError,AdapterException) as error:
            report['conflicts'].append(dict(feed_id=feed['id'],reason=str(error)))
