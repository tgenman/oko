"""Current Publication summaries, their provenance and processing work."""
import json
from typing import Any,TYPE_CHECKING
from oko.storage import material_read
from oko.storage.reading import reading_value
from oko.storage import processing_input as publication_input
from oko.processing.contracts import ProcessingRequest

if TYPE_CHECKING:
    from oko.storage.store import Store


class SummaryResults:
    def __init__(self,store: 'Store') -> None:
        self.store=store

    @reading_value
    def signature(self,item: str) -> str | None:
        return publication_input.signature(self.store,item,'theses')


    def request(self,item: str) -> tuple[ProcessingRequest,str,bool] | None:
        signature=self.signature(item)
        row=self.store.db.execute('SELECT signature FROM thesis_inputs WHERE item=?',(item,)).fetchone()
        if not signature or not row or row[0]!=signature:
            return None
        values,public_ai,version=publication_input.request_settings(self.store,item)
        request=ProcessingRequest(sources=publication_input.sources(self.store,item,'theses'),settings_version=version,
            prompt=values['prompt_theses']['value'],processor=values['processor']['value'],generation=0)
        return request,signature,public_ai


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
