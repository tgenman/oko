"""Persistence of a discovered publication page in the caller's transaction."""
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
import json

from oko.collection.adapters import ExternalReference, PublicationContent
from oko.collection.telegram.contracts import HistoryEnvelope
from oko.domain.collection import CollectionEffects
from oko.storage.collection_diagnostics import CollectionDiagnostics
from oko.storage.database import Connection, expect_row
from oko.storage import memberships as membership_graph
from oko.storage.reader_status import changed as status_changed
from oko.storage.publications import Publications
from oko.storage.subscriptions import SubscriptionRepository
from oko.storage.reader_versions import ReaderVersions
from oko.storage.collection_effects import CollectionEffectCapture
from oko.storage.history_publication import loaded as history_loaded


class DiscoveryRepository:
    def __init__(self, *, db: Connection, publications: Publications, subscriptions: SubscriptionRepository,
                 versions: ReaderVersions, effects: CollectionEffectCapture,
                 root: Path, unviewed: Callable[[list[str]],None],
                 clock: Callable[[], float], fault: Callable[[str], None]) -> None:
        self._db, self._publications, self._subscriptions = db, publications, subscriptions
        self._versions, self._effects = versions, effects
        self._root, self._clock, self._fault, self._unviewed = root, clock, fault, unviewed

    def accept(self,publications: Sequence[PublicationContent],cursor: str,feed: str,namespace: str = '',*,description: str | None = None,successful: bool = True) -> CollectionEffects:
        try:
            return self._accept(publications,cursor,feed,namespace,description=description,successful=successful)
        except BaseException:
            if self._db.transaction_depth:
                self._db.transaction_failed = True
            raise

    def _accept(self,publications: Sequence[PublicationContent],cursor: str,feed: str,namespace: str = '',*,description: str | None = None,successful: bool = True) -> CollectionEffects:
        if not self._db.in_transaction or not self._db.transaction_depth:
            raise RuntimeError('Discovery acceptance requires an owning transaction')
        effects=CollectionDiagnostics()
        membership_graph.lock_graph(self._db)
        history_items=[]
        item_status_changed = False
        saved_items: set[str] = set()
        added_memberships: list[str] = []
        completed_links: set[str] = set()
        envelope=HistoryEnvelope.decode(cursor,adapter='web') if namespace=='telegram-web' else None
        selection=json.loads(cursor) if not envelope and self._subscriptions.history.status(feed) else {}
        history_row=self._db.execute('SELECT selection FROM initial_history WHERE feed=%s',(feed,)).fetchone()
        saved_selection=json.loads(history_row['selection']) if history_row else {}
        result_run=envelope.position.get('run') if envelope else selection.get('run')
        stale_history=bool(saved_selection.get('restore_unread') and result_run and result_run!=saved_selection.get('run'))
        enrolled=set(envelope.selection.enrolled_ids) if envelope else set(selection.get('history_enroll',[]))
        if stale_history:enrolled.clear()
        for publication in publications:
            ref=publication.metadata.reference
            identity=ref.key.external_id
            url=ref.url
            members=tuple(publication.content.get('extra',{}).get('telegram',{}).get('members',()))
            published=publication.metadata.published_at.timestamp() if publication.metadata.published_at else None
            needs_fetch=publication.needs_fetch
            # Earlier entries can create linked identities in this page.
            plan=self._publications.plan(feed,[publication]).get(identity)
            if plan is None:
                continue
            item=plan.item
            self._effects.discovery(item, plan, effects)
            initial = {'extra':{'telegram':{'members':list(members)}}} if ref.key.platform.value=='telegram' else {}
            inserted = bool(self._db.execute('INSERT INTO publications (id, url, content, platform, external_id, published_at, discovered_at) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (item, url, json.dumps(initial), ref.key.platform.value, ref.key.external_id, published, self._clock())).rowcount)
            item_status_changed |= inserted
            membership=membership_graph.add(self._db,feed,item,newly_created=inserted)
            if membership:
                added_memberships.append(item)
                saved_items.add(item)
            self._publications.identify(item,ref,effects=effects)
            if plan.content is not None:
                previous=expect_row(self._db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone())
                completed='blocks' not in previous['content']
                self._publications.publish(item,plan.content,effects)
                effects.invalidated_fetch.add(item)
                if needs_fetch:
                    self._db.execute("UPDATE publications SET status='pending_collection' WHERE id=%s",(item,))
                item_status_changed |= completed
                if completed:
                    completed_links.add(item)
                saved_items.add(item)
            row=expect_row(self._db.execute("SELECT *, COALESCE(content #> '{extra,telegram,members}','[]'::jsonb)::text AS members FROM publications WHERE id = %s",(item,)).fetchone())
            if published is not None and row['published_at'] is None:
                self._db.execute('UPDATE publications SET published_at = %s WHERE id = %s',(published,item))
                item_status_changed = True
            if identity in enrolled:
                history_items.append(item)
            if members and set(members)!=set(json.loads(row['members'])):
                needs_fetch=True
            if needs_fetch and row['status']=='pending_collection':
                merged=sorted(set(json.loads(row['members']))|set(members))
                if merged!=json.loads(row['members']):
                    self._db.execute("UPDATE publications SET content=jsonb_set(content,'{extra}',COALESCE(content->'extra','{}'::jsonb) || jsonb_build_object('telegram',COALESCE(content #> '{extra,telegram}','{}'::jsonb) || jsonb_build_object('members',%s::jsonb)),true) WHERE id = %s",(json.dumps(merged),item))
                    item_status_changed = True
                    saved_items.add(item)
                    effects.fetch[item]=True
                effects.fetch.setdefault(item,False)
        if item_status_changed:
            status_changed(self._db)
        self._versions.memberships_changed(added_memberships)
        if not stale_history and (envelope or 'history_enroll' in selection):
            if saved_selection.get('restore_unread') and history_items:
                enrolled_before={row[0] for row in self._db.execute('SELECT item FROM history_members WHERE feed=%s AND item=ANY(%s)',(feed,history_items))}
                self._unviewed([item for item in history_items if item not in enrolled_before])
            self._subscriptions.history.enroll(feed,history_items,envelope.selection.scan_complete if envelope else selection['scan_complete'])
            if envelope:
                self._subscriptions.history.telegram_state(feed,envelope.selection)
            for item in history_items:
                row=expect_row(self._db.execute('SELECT content FROM publications WHERE id = %s',(item,)).fetchone())
                if 'blocks' in row['content']:
                    self._subscriptions.history.saved(item,history_loaded(self._db,self._root,item))
        if selection and not stale_history:
            self._subscriptions.history.discovery_state(feed,selection)
        effects.processing_items.update(saved_items)
        for item in set(added_memberships) | completed_links:
            effects.parent_notifications[item] = False
        if successful:
            self._subscriptions.details.record(feed,'updated')
        self._subscriptions.details.description(feed,description)
        self._fault('before_discovery_commit')
        if not stale_history:self._db.execute('UPDATE feeds SET cursor = %s WHERE id = %s',(cursor,feed))

        return self._effects.finish(effects,operation='discover',target=feed)
