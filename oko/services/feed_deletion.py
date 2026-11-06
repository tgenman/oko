"""Finish feed deletion using separate Job, Publication and Feed capabilities."""
from typing import Generic, Protocol, TypeVar
from oko.domain.job import Job


class FeedDeletionPlan(Protocol):
    feed: str
    publication_ids: list[str]


Plan = TypeVar('Plan',bound=FeedDeletionPlan)


class DeletionJobs(Protocol[Plan]):
    def prepare_feed_deletion(self, job: Job) -> Plan | None: ...
    def remove_feed_jobs(self, plan: Plan) -> None: ...
    def complete_feed_deletion(self, plan: Plan, deleted: bool) -> None: ...


class DeletionPublications(Protocol):
    def remove_cleaned(self, identities: list[str]) -> None: ...


class DeletionFeeds(Protocol):
    def remove_cleaned_feed(self, feed: str) -> bool: ...


class FeedDeletion(Generic[Plan]):
    def __init__(self, jobs: DeletionJobs[Plan], publications: DeletionPublications, feeds: DeletionFeeds) -> None:
        self.jobs,self.publications,self.feeds=jobs,publications,feeds

    def finish(self, job: Job) -> bool:
        plan=self.jobs.prepare_feed_deletion(job)
        if plan is None:
            return False
        self.publications.remove_cleaned(plan.publication_ids)
        self.jobs.remove_feed_jobs(plan)
        deleted=self.feeds.remove_cleaned_feed(plan.feed)
        self.jobs.complete_feed_deletion(plan,deleted)
        return True
