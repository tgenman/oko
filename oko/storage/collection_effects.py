"""Capture existing diagnostic effects before collection changes them."""
from typing import TYPE_CHECKING

from oko.domain.collection import CollectionEffects
from oko.storage.collection_diagnostics import CollectionDiagnostics
from oko.storage import publication_links
from oko.storage.publications import DiscoveryProjection

if TYPE_CHECKING:
    from oko.storage.store import Store


class CollectionEffectCapture:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def finish(self, diagnostics: CollectionDiagnostics, *, operation: str, target: str) -> CollectionEffects:
        return self._store.jobs.register_collection_capture(diagnostics, operation=operation, target=target)

    def discovery(self, item: str, plan: DiscoveryProjection, effects: CollectionDiagnostics) -> None:
        store = self._store
        if plan.new_source_jobs:
            from oko.storage import source_groups
            source_ids=[job_id for job_id in plan.new_source_jobs if job_id not in effects.source_groups_before]
            for start in range(0,len(source_ids),400):
                group=source_ids[start:start+400]
                effects.source_groups_before.update(source_groups.job_keys_many(store,group))
                evidence=source_groups.job_evidence_many(store,[(job_id,None) for job_id in group])
                for key,objects in evidence.items():effects.source_evidence_before.setdefault(key,set()).update(objects)
        if item not in effects.delete_problems_checked:
            if plan.delete_problem_job is not None:
                effects.delete_problem_ids_before[item] = store.problems.deletion_problem_ids(plan.delete_problem_job,store.wall_clock())
            effects.delete_problems_checked.add(item)
        if plan.delete_problem_children:
            publication_links.capture_deletion_problems(store,effects,set(plan.delete_problem_children))
        if plan.fetch_problem_job is not None and plan.fetch_problem_job not in effects.fetch_problems_checked:
            from oko.storage.fetch_job_problems import managed_candidates,previous_candidates
            effects.fetch_problems_checked.add(plan.fetch_problem_job)
            previous_fetch=managed_candidates(store,plan.fetch_problem_job,store.wall_clock())
            if any(candidate.get('fetch_youtube_event') for candidate in previous_fetch):
                previous_fetch=previous_candidates(store,plan.fetch_problem_job,store.wall_clock())
            if previous_fetch:effects.fetch_problems_before[plan.fetch_problem_job]=previous_fetch
        if plan.fetch_problem_children:
            publication_links.capture_fetch_problems(store,effects,set(plan.fetch_problem_children))
        if plan.media_problem_exists:
            publication_links.capture_media_problems(store,effects,{item})
        if plan.media_problem_children:
            publication_links.capture_media_problems(store,effects,set(plan.media_problem_children))
        if plan.source_problem_exists:
            publication_links.capture_source_groups(store,effects,{item})
        if plan.processing_problem_exists:
            publication_links.capture_processing_problems(store,effects,{item})
        if plan.processing_problem_children:
            publication_links.capture_processing_problems(store,effects,set(plan.processing_problem_children))
