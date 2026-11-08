"""Batched reader facts over the existing PostgreSQL/file storage."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from oko.storage.publication_owners import owned_ids, owner_expr
from oko.storage.store import Store
from oko.domain.reader import WorkSummary
from oko.processing.config import ProcessorConfig
from oko.storage.problems import CommandRecord
from oko.storage import material_read, publication_links
from oko.storage.reading import reader_now

class ReaderFacts:
    def __init__(self, storage: Callable[[], Store]) -> None:
        self.storage = storage

    def card(self, identity: str) -> dict[str, Any] | None:
        from oko.storage.card_read import card
        return card(self.storage(), identity)


    def unified_publication(self, identity: str) -> dict[str, Any]:
        from oko.storage.unified_publication import project
        store=self.storage()
        value=project(store.db,store.root,identity,store.clock(),store.wall_clock())
        row=material_read.item(store.db,identity)
        if row and row['feed_id'] is not None:
            effective=store.subscriptions.effective('feed',row['feed_id'],details=False)['effective']
            for section,kind,setting in (('filter','filter','prompt_filters'),('summary','theses','prompt_theses')):
                metadata=value['processing_metadata'].get(section)
                if not isinstance(metadata,dict):continue
                from oko.storage import processing_input
                source_hash=processing_input.input_hash(store,identity,kind)
                stale=bool(metadata.get('preserve_stale') or metadata.get('source_url_stale') or metadata.get('source_title_stale')
                    or metadata.get('prompt') is not None and metadata['prompt'] != effective[setting]['value']
                    or source_hash is not None and metadata.get('input_hash') is not None and metadata['input_hash']!=source_hash)
                metadata['stale']=stale
        files={item['id']:item for item in value['media']}
        for owner in [{'id':identity,'content':value['content']},*value['linked_publications']]:
            for block in owner['content'].get('blocks',[]):
                file=files.get(block.get('media_id')) if block.get('type')=='media' and block.get('role')=='subtitles' else None
                if file and file['status']=='ready' and file['url'] is None:
                    store.processing.repair(owner['id'])
        return value

    def content_version(self, identity: str) -> str:
        return self.storage().content_version(identity)

    def processor_config(self) -> ProcessorConfig | None:
        return self.storage().processor_config()

    def work_summary(self) -> list[WorkSummary]:
        return self.storage().work_summary()

    def command_record(self, identity: str) -> CommandRecord | None:
        return self.storage().problems.command_record(identity)

    def command_succeeded(self, context: str) -> bool:
        return bool(self.storage().db.execute("SELECT 1 FROM reader_action_results WHERE identity=%s AND result='succeeded' LIMIT 1",(context,)).fetchone())

    def clock(self) -> float:
        return self.storage().clock()

    def wall_clock(self) -> float:
        return self.storage().wall_clock()

    def item(self, identity: str) -> dict[str, Any] | None:
        row = material_read.item(self.storage().db, identity)
        return dict(row) if row else None

    def jobs(self, identity: str) -> dict[str, dict[str, Any]]:
        return {kind: dict(row) for kind, row in material_read.jobs(self.storage().db, identity).items()}

    def feed(self, identity: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return material_read.feed(self.storage().db, identity)

    @contextmanager
    def inputs(self, identities: set[str], *, include_subtitles: bool = True) -> Iterator[None]:
        with self.storage().db.access_lock, material_read.material_inputs(self.storage().db, identities, include_subtitles=include_subtitles):
            yield

    def card_validators(self, identities: list[str]) -> dict[str, tuple[Any, ...]]:
        if not identities:
            return {}
        store = self.storage()
        now = reader_now(store.clock())
        rows=store.db.execute("""SELECT i.id,i.xmin::text AS row_generation,i.feed_id,f.kind,f.transport,
            j.status AS fetch_status,CASE WHEN j.status='pending' AND j.next>%s THEN j.next END AS fetch_deadline,
            media.loading AS media_loading,media.deadline AS media_deadline,linked.children
            FROM publications i
            LEFT JOIN feeds f ON f.id=i.feed_id
            LEFT JOIN jobs j ON j.kind='fetch' AND j.item=i.id
            LEFT JOIN LATERAL (SELECT coalesce(bool_or(mj.status='running' OR (mj.status='pending' AND mj.next<=%s)),false) AS loading,
                min(mj.next) FILTER (WHERE mj.status='pending' AND mj.next>%s) AS deadline
                FROM media_files m JOIN jobs mj ON mj.item=m.id AND mj.kind='download_media' WHERE m.publication_id=i.id) media ON true
            LEFT JOIN LATERAL (SELECT coalesce(jsonb_agg(jsonb_build_array(child.id,child.xmin::text) ORDER BY child.id),'[]'::jsonb) AS children
                FROM publication_links l JOIN publications child ON child.id=l.target_publication_id
                WHERE l.source_publication_id=i.id) linked ON true
            WHERE i.id=ANY(%s)""", (now,now,now,identities))
        return {row['id']:(row['row_generation'],row['feed_id'],row['kind'],row['transport'],
                row['fetch_status'],row['fetch_deadline'],row['media_loading'],row['media_deadline'],row['children']) for row in rows}

    def feed_resources(self, identities: set[str] | None = None) -> dict[str, tuple[dict[str, Any], dict[str, Any] | None]]:
        subscriptions = self.storage().subscriptions
        rows = subscriptions.feeds_with_history() if identities is None else subscriptions.feeds_many(identities)
        bindings = {}
        for start in range(0, len(rows), 400):
            bindings.update(subscriptions.telegram.bindings([row['id'] for row in rows[start:start+400]]))
        return {row['id']: (row, bindings.get(row['id'])) for row in rows}

    def avatar(self, feed: str, revision: str) -> tuple[bytes, str] | None:
        row=self.storage().db.execute("SELECT avatar_image AS image, avatar_mime AS mime FROM feeds WHERE id = %s AND avatar_digest = %s AND NOT deleted", (feed, revision)).fetchone()
        return (bytes(row['image']), row['mime']) if row else None

    def material_feeds(self, identities: set[str]) -> dict[str, str]:
        return {row['id']: row['feed_id'] for row in self.storage().db.execute(f"SELECT i.id , {owner_expr('i')} AS feed_id FROM publications i WHERE i.id IN ( {','.join(('%s' for _ in identities))} )", tuple(identities))} if identities else {}


    def hidden_problem_objects(self, materials: set[str], feeds: set[str]) -> tuple[set[str],set[str]]:
        db=self.storage().db
        hidden_materials={row['id'] for row in db.execute('SELECT p.id FROM publications p JOIN feeds f ON f.id=p.feed_id WHERE f.deleted AND p.id=ANY(%s)',(sorted(materials),))} if materials else set()
        hidden_feeds={row['id'] for row in db.execute('SELECT id FROM feeds WHERE deleted AND id=ANY(%s)',(sorted(feeds),))} if feeds else set()
        return hidden_materials,hidden_feeds

    def was_viewed(self, identity: str) -> bool:
        return self.storage().db.execute('SELECT 1 FROM publications WHERE id = %s', (identity,)).fetchone() is None

    def viewed_ids(self) -> list[str]:
        # Completed roots are removed or retained only as linked dependencies;
        # neither case is a persisted list of viewed publication identities.
        return []

    def feed_members(self, identity: str) -> set[str]:
        return {row[0] for row in self.storage().db.execute(f'''SELECT id FROM publications WHERE feed_id=%s
            UNION SELECT id FROM ({owned_ids('= %s')}) owner''', (identity,identity,identity))}

    def deletion_feeds(self) -> set[str]:
        return {row[0] for row in self.storage().db.execute(f"SELECT DISTINCT {owner_expr('i')} AS feed_id FROM publications i WHERE i.id IN ( SELECT item FROM jobs WHERE kind = 'delete' AND status != 'done' )")}
