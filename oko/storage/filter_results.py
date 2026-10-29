"""Current filtered Publication content and its processing work."""
import json
from typing import Any,TYPE_CHECKING
from oko.storage import material_read
from oko.storage.reading import reading_value
from oko.storage import processing_input as publication_input
from oko.processing.contracts import ProcessingRequest

if TYPE_CHECKING:
    from oko.storage.store import Store


class FilterResults:
    def __init__(self,store: 'Store') -> None:
        self.store=store

    @reading_value
    def signature(self,item: str) -> str | None:
        return publication_input.signature(self.store,item,'filter')


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
        enabled = publication_input.stage_enabled(self.store, item, 'filter')
        if not enabled and (not job or job['status'] != 'running'):
            return dict(status='off', hidden=False, reason='', removed=[], error=job['error'] if job else None)
        return dict(status=('off' if job and job['status']=='blocked' else job['status']) if job and job['status']!='done' else 'pending',error=job['error'] if job else None,
                    hidden=False,reason='',removed=[])

    def request(self,item: str) -> tuple[ProcessingRequest,str,bool] | None:
        signature=self.signature(item)
        row=self.store.db.execute('SELECT signature FROM filter_inputs WHERE item = %s',(item,)).fetchone()
        if not signature or not row or row[0]!=signature:
            return None
        values,public_ai,version=publication_input.request_settings(self.store,item)
        request=ProcessingRequest(operation='filter',sources=publication_input.sources(self.store,item,'filter'),
            settings_version=version,prompt=values['prompt_filters']['value'],processor=values['processor']['value'],generation=0)
        return request,signature,public_ai


    def forget(self,item: str) -> None:
        self.store.db.execute('DELETE FROM filter_inputs WHERE item = %s',(item,))
