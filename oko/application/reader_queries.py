"""Bind reader queries to their focused repository dependencies."""
from collections.abc import Callable

from oko.services.queries import ReadingQueries
from oko.storage.reader_facts import ReaderFacts
from oko.storage.store import Store


def reader_queries(storage: Callable[[], Store]) -> ReadingQueries:
    return ReadingQueries(access=lambda: storage().db.access_lock, facts=ReaderFacts(storage),
        subscriptions=lambda: storage().subscriptions, reading=lambda: storage().reading,
        publications=lambda: storage().publications, filters=lambda: storage().filters,
        processing=lambda: storage().processing, media=lambda: storage().media,
        versions=lambda: storage().read_versions)
