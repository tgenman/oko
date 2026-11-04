"""Persistence of a discovered publication page in the caller's transaction."""
from collections.abc import Callable
from typing import Any
import json

from oko.collection.contracts import Material, Publication
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


class DiscoveryRepository:
    def __init__(self, *, db: Connection, publications: Publications, subscriptions: SubscriptionRepository,
                 versions: ReaderVersions, effects: CollectionEffectCapture,
                 needs_collection: Callable[[str, Material | None], bool], original: Callable[[str], tuple[dict[str, Any], str]],
                 clock: Callable[[], float], fault: Callable[[str], None]) -> None:
        self._db, self._publications, self._subscriptions = db, publications, subscriptions
        self._versions, self._effects = versions, effects
        self._needs_collection, self._original, self._clock, self._fault = needs_collection, original, clock, fault

    def accept(self,publications: list[Publication],cursor: str,feed: str,namespace: str = '',*,description: str | None = None,successful: bool = True) -> CollectionEffects:
        try:
            return self._accept(publications,cursor,feed,namespace,description=description,successful=successful)
        except BaseException:
            if self._db.transaction_depth:
                self._db.transaction_failed = True
            raise

    def _accept(self,publications: list[Publication],cursor: str,feed: str,namespace: str = '',*,description: str | None = None,successful: bool = True) -> CollectionEffects:
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
        enrolled=set(envelope.selection.enrolled_ids) if envelope else set(selection.get('history_enroll',[]))
        for publication in publications:
            # Earlier entries can create linked identities in this page.
            plan=self._publications.plan(feed,[publication]).get(publication.id)
            if plan is None:
                continue
            item=plan.item
            self._effects.discovery(item, plan, effects)
            if publication.adapter_reference is None:
                raise ValueError('Adapter did not return a publication external key')
            ref = publication.adapter_reference
            inserted = bool(self._db.execute('INSERT INTO publications (id, url, members, platform, external_id, published_at, discovered_at) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (item, publication.url, json.dumps(publication.members), ref.key.platform.value, ref.key.external_id, publication.published if publication.published_known else None, self._clock())).rowcount)
            item_status_changed |= inserted
            membership=membership_graph.add(self._db,feed,item,newly_created=inserted)
            if membership:
                added_memberships.append(item)
                saved_items.add(item)
            if publication.adapter_reference is None:
                raise ValueError('Adapter did not return a publication external key')
            self._publications.identify(item,publication.adapter_reference,effects=effects)
            if plan.content is not None:
                assert plan.fingerprint is not None
                self._publications.publish(item,plan.content,plan.fingerprint,effects)
                completed = bool(self._db.execute('UPDATE publications SET complete = 1 WHERE id = %s AND complete IS DISTINCT FROM 1',(item,)).rowcount)
                item_status_changed |= completed
                if completed:
                    completed_links.add(item)
                saved_items.add(item)
            row=expect_row(self._db.execute('SELECT * FROM publications WHERE id = %s',(item,)).fetchone())
            if publication.published_known and row['published_at'] is None:
                self._db.execute('UPDATE publications SET published_at = %s WHERE id = %s',(publication.published,item))
                item_status_changed = True
            if publication.id in enrolled:
                history_items.append(item)
            needs_fetch=publication.teaser is None or publication.teaser.adapter_content is None or publication.teaser.adapter_content.needs_fetch
            if publication.members and set(publication.members)!=set(json.loads(row['members'])):
                needs_fetch=True
            if publication.observed_only:
                needs_fetch=self._needs_collection(item,publication.teaser)
            if needs_fetch and not self._publications.source_deleted(item):
                members=sorted(set(json.loads(row['members']))|set(publication.members))
                if members!=json.loads(row['members']):
                    self._db.execute('UPDATE publications SET members = %s WHERE id = %s',(json.dumps(members),item))
                    item_status_changed = True
                    saved_items.add(item)
                    effects.fetch[item]=True
                effects.fetch.setdefault(item,False)
        if item_status_changed:
            status_changed(self._db)
        self._versions.memberships_changed(added_memberships)
        if envelope or 'history_enroll' in selection:
            self._subscriptions.history.enroll(feed,history_items,envelope.selection.scan_complete if envelope else selection['scan_complete'])
            if envelope:
                self._subscriptions.history.telegram_state(feed,envelope.selection)
            for item in history_items:
                row=expect_row(self._db.execute('SELECT complete FROM publications WHERE id = %s',(item,)).fetchone())
                if self._publications.source_deleted(item):
                    self._subscriptions.history.failed(item,unavailable=True)
                elif row['complete']:
                    self._subscriptions.history.saved(item,bool(self._original(item)[0].get('full')))
        if selection:
            self._subscriptions.history.discovery_state(feed,selection)
        effects.processing_items.update(saved_items)
        for item in set(added_memberships) | completed_links:
            effects.parent_notifications[item] = False
        if successful:
            self._subscriptions.details.record(feed,'updated')
        self._subscriptions.details.description(feed,description)
        self._fault('before_discovery_commit')
        self._db.execute('UPDATE feeds SET cursor = %s WHERE id = %s',(cursor,feed))

        return self._effects.finish(effects,operation='discover',target=feed)
