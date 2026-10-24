"""Current Publication summaries, their provenance and processing work."""
import json
from typing import Any,TYPE_CHECKING
from oko.storage import material_read
from oko.storage.reading import reading_value
from oko.reviews import publication_input
from oko.processing.contracts import ProcessingRequest,Theses

if TYPE_CHECKING:
    from oko.storage.store import Store,Job


class PublicationTheses:
    def __init__(self,store: 'Store') -> None:
        self.store=store

    @reading_value
    def signature(self,item: str) -> str | None:
        return publication_input.signature(self.store,item,'theses')

    def sync(self,items: list[str]) -> None:
        store=self.store
        for item in items:
            signature=self.signature(item)
            old=store.db.execute('SELECT signature FROM thesis_inputs WHERE item=?',(item,)).fetchone()
            job=store.db.execute("SELECT status FROM jobs WHERE kind='theses' AND item=?",(item,)).fetchone()
            if signature is None and old is None and (not job or job[0]=='cancelled'):
                continue
            if old and old[0]==signature and job and (job[0] not in ('done','cancelled') or self.read(item) is not None):
                continue
            with store.db:
                store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='theses' AND item=?",(item,))
                if signature:
                    store.db.execute('INSERT INTO thesis_inputs VALUES (?,?) ON CONFLICT(item) DO UPDATE SET signature=excluded.signature',(item,signature))
                    store.db.execute("INSERT INTO jobs(kind,item) VALUES ('theses',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0,error=NULL",(item,))
                else:
                    store.db.execute('DELETE FROM thesis_inputs WHERE item=?',(item,))

    def request(self,job: 'Job') -> tuple[ProcessingRequest,str,bool] | None:
        signature=self.signature(job.item)
        row=self.store.db.execute('SELECT signature FROM thesis_inputs WHERE item=?',(job.item,)).fetchone()
        if not signature or not row or row[0]!=signature:
            return None
        values,public_ai,version=publication_input.request_settings(self.store,job.item)
        request=ProcessingRequest(sources=publication_input.sources(self.store,job.item,'theses'),settings_version=version,
            prompt=values['prompt_theses']['value'],processor=values['processor']['value'],generation=job.generation)
        return request,signature,public_ai

    def retry(self,item: str) -> bool:
        row=material_read.item(self.store.db,item)
        if row and json.loads(row['processing_metadata']).get('summary',{}).get('reason')=='no_input':
            return True
        if not self.signature(item):
            return False
        self.store.forget_execution('theses',item)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='pending',next=0,retry_at=NULL,error=NULL,generation=generation+1 WHERE kind='theses' AND item=?",(item,))
            self.store.db.execute("UPDATE items SET status='pending_summary' WHERE id=?",(item,))
        return True

    @reading_value
    def status(self,item: str) -> dict[str,Any] | None:
        if not self.signature(item):
            return None
        job=material_read.jobs(self.store.db,item).get('theses')
        return dict(status=job['status'],error=job['error']) if job else dict(status='pending',error=None)

    @reading_value
    def read(self,item: str) -> dict[str,Any] | None:
        row=material_read.item(self.store.db,item)
        metadata=json.loads(row['processing_metadata']).get('summary',{}) if row else {}
        if not metadata or metadata.get('signature')!=self.signature(item):
            return None
        assert row is not None
        result=json.loads(row['summarized_content'])
        identities={identity for point in result['points'] for identity in point['source_publication_ids']}
        urls={identity:record['url'] for identity in identities if (record:=material_read.item(self.store.db,identity)) is not None}
        return dict(points=[dict(text=p['text'],sources=[dict(id=identity,url=urls[identity]) for identity in p['source_publication_ids']]) for p in result['points']])

    def provenance(self,item: str) -> dict[str,Any] | None:
        return self.store.publications.provenance(item,'theses') if self.read(item) is not None else None

    def save(self,job: 'Job',signature: str,theses: Theses) -> None:
        if self.store.valid(job) and signature==self.signature(job.item):
            self.store.save_derivative(job,signature,theses.model_dump(),'\n'.join('- '+p.text for p in theses.points))
