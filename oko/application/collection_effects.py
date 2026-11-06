"""Apply collected consequences without exposing storage to domain services."""
from typing import Generic, Protocol, TypeVar

from oko.domain.collection import CollectionEffects
from oko.domain.job import Job


State = TypeVar('State')


class CollectionJobs(Protocol[State]):
    def begin_collection_effects(self, effects: CollectionEffects, job: Job) -> State: ...
    def reject_collection_effects(self) -> None: ...
    def mark_media_unavailable(self, identity: str, code: str, message: str) -> None: ...
    def invalidate_source_metadata_inputs(self, item: str, *, title: bool = False, preserve_cancelled: bool = False) -> None: ...
    def schedule_collection_effects(self, effects: CollectionEffects, state: State) -> None: ...
    def complete_collection_effects(self, effects: CollectionEffects, state: State) -> None: ...


class CollectionPublications(Protocol):
    def notify_parents(self, item: str, *, preserve_cancelled: bool = False) -> None: ...
    def status(self, item: str) -> None: ...


class CollectionProcessing(Protocol):
    def reconcile_accepted_item(self, item: str, *, preserve_cancelled: bool = False) -> None: ...
    def sync(self) -> None: ...


class CollectionApplication(Generic[State]):
    def __init__(self, jobs: CollectionJobs[State], publications: CollectionPublications, processing: CollectionProcessing) -> None:
        self.jobs,self.publications,self.processing=jobs,publications,processing

    def apply(self, effects: CollectionEffects, *, job: Job) -> None:
        try:
            state=self.jobs.begin_collection_effects(effects,job)
            for identity,(code,message) in effects.unavailable_media.items():
                self.jobs.mark_media_unavailable(identity,code,message)
            for item in effects.source_url_changes:
                self.jobs.invalidate_source_metadata_inputs(item)
            for item,preserve in effects.source_title_changes.items():
                self.jobs.invalidate_source_metadata_inputs(item,title=True,preserve_cancelled=preserve)
            for item,preserve in effects.parent_notifications.items():
                self.publications.notify_parents(item,preserve_cancelled=preserve)
            self.jobs.schedule_collection_effects(effects,state)
            for item in sorted(effects.processing_items):
                self.processing.reconcile_accepted_item(item)
            if effects.sync_processing:
                self.processing.sync()
            for item in sorted(effects.refresh_status):
                self.publications.status(item)
            self.jobs.complete_collection_effects(effects,state)
        except BaseException:
            self.jobs.reject_collection_effects()
            raise
