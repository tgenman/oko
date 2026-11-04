"""Bind processing service capabilities to the current storage instance."""
from collections.abc import Callable

from oko.services.processing import ProcessingService
from oko.storage.processing_state import ProcessingRepository
from oko.storage.store import Store


def processing_service(storage: Callable[[], Store]) -> ProcessingService:
    return ProcessingService(access=lambda: storage().db.access_lock,
                             repository=lambda: ProcessingRepository(storage()))
