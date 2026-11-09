"""Coordinate a manual feed refresh through focused persistence capabilities."""
from typing import Protocol


class RefreshMedia(Protocol):
    def publications_for_feed(self, feed: str) -> list[str]: ...


class RefreshHistory(Protocol):
    def resume_history(self, feed: str) -> bool: ...


class RefreshJobs(Protocol):
    def retry_media(self, item: str, *, revalidate_ready: bool = True) -> None: ...
    def refresh_discovery(self, feed: str) -> None: ...
    def retry_feed_fetches(self, feed: str) -> None: ...
    def retry_feed_processing(self, feed: str) -> None: ...


class FeedRefresh:
    def __init__(self, media: RefreshMedia, history: RefreshHistory, jobs: RefreshJobs) -> None:
        self.media, self.history, self.jobs = media, history, jobs

    def refresh(self, feed: str) -> None:
        for publication in self.media.publications_for_feed(feed):
            self.jobs.retry_media(publication,revalidate_ready=False)
        if self.history.resume_history(feed):
            self.jobs.refresh_discovery(feed)
        self.jobs.retry_feed_fetches(feed)
        self.jobs.retry_feed_processing(feed)
