"""Current filtered Publication content and its processing work."""
import json
from typing import Any,TYPE_CHECKING
from oko.storage import material_read
from oko.storage.reading import reading_value
from oko.reviews import publication_input
from oko.processing.contracts import FilterResult,ProcessingRequest

if TYPE_CHECKING:
    from oko.storage.store import Store,Job


class Filters:
    def __init__(self,store: 'Store') -> None:
        self.store=store
        store.db.execute('CREATE TABLE IF NOT EXISTS filter_inputs (item TEXT PRIMARY KEY,signature TEXT)')

    @reading_value
    def signature(self,item: str) -> str | None:
        return publication_input.signature(self.store,item,'filter')

    def sync(self,items: list[str]) -> None:
        store=self.store
        for item in items:
            signature=self.signature(item)
            old=store.db.execute('SELECT signature FROM filter_inputs WHERE item=?',(item,)).fetchone()
            job=store.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=?",(item,)).fetchone()
            if signature is None and old is None and (not job or job['status']=='cancelled'):
                continue
            if old and old[0]==signature and job:
                result=self.read(item)
                store.read_versions.sync_filter(item,bool(result['hidden']))
                if job['status'] not in ('done','cancelled') or result['status']=='done':
                    continue
            store.read_versions.sync_filter(item,False)
            with store.db:
                store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='filter' AND item=?",(item,))
                if signature:
                    store.db.execute('INSERT INTO filter_inputs VALUES (?,?) ON CONFLICT(item) DO UPDATE SET signature=excluded.signature',(item,signature))
                    store.db.execute("INSERT INTO jobs(kind,item) VALUES ('filter',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0,error=NULL",(item,))
                else:
                    store.db.execute('DELETE FROM filter_inputs WHERE item=?',(item,))

    @reading_value
    def read(self,item: str) -> dict[str,Any]:
        signature=self.signature(item)
        if not signature:
            return dict(status='off',hidden=False,reason='',removed=[])
        row=material_read.item(self.store.db,item)
        metadata=json.loads(row['processing_metadata']).get('filter',{}) if row else {}
        if row is not None and metadata.get('signature')==signature:
            result=json.loads(row['filtered_content'])
            return dict(status='done',hidden=result.get('hidden',False),
                        reason=result['reason'],removed=[part['text'] for part in result['removed']],error=None)
        job=material_read.jobs(self.store.db,item).get('filter')
        return dict(status=job['status'] if job and job['status']!='done' else 'pending',error=job['error'] if job else None,
                    hidden=False,reason='',removed=[])

    def request(self,job: 'Job') -> tuple[ProcessingRequest,str,bool] | None:
        signature=self.signature(job.item)
        row=self.store.db.execute('SELECT signature FROM filter_inputs WHERE item=?',(job.item,)).fetchone()
        if not signature or not row or row[0]!=signature:
            return None
        values,public_ai,version=publication_input.request_settings(self.store,job.item)
        request=ProcessingRequest(operation='filter',sources=publication_input.sources(self.store,job.item,'filter'),
            settings_version=version,prompt=values['prompt_filters']['value'],processor=values['processor']['value'],generation=job.generation)
        return request,signature,public_ai

    def save(self,job: 'Job',signature: str,result: FilterResult) -> None:
        if self.store.valid(job) and signature==self.signature(job.item):
            self.store.save_derivative(job,signature,result.model_dump(),result.reason)

    def retry(self,item: str) -> bool:
        row=material_read.item(self.store.db,item)
        if row and json.loads(row['processing_metadata']).get('filter',{}).get('reason')=='no_input':
            return True
        if not self.signature(item):
            return False
        self.store.forget_execution('filter',item)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='pending',next=0,retry_at=NULL,error=NULL,generation=generation+1 WHERE kind='filter' AND item=?",(item,))
        self.store.processing.sync()
        return True

    def forget(self,item: str) -> None:
        self.store.db.execute('DELETE FROM filter_inputs WHERE item=?',(item,))
