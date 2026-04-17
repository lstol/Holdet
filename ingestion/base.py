"""
ingestion/base.py — Abstract interface for all data ingestion sources.

Concrete implementations: api.py (primary), manual.py, image.py.
All must return list[Rider] so the rest of the pipeline is source-agnostic.
"""
from abc import ABC, abstractmethod

from scoring.engine import Rider


class IngestionSource(ABC):
    """Abstract base class for rider data ingestion."""

    @abstractmethod
    def fetch_riders(self, game_id: str) -> list:
        """
        Fetch all riders for the given game_id.

        Returns list[Rider] with fields populated from the source.
        Fields unavailable in the source (e.g. gc_position, jerseys for
        the API endpoint) must be set to their null equivalents:
          gc_position=None, jerseys=[], in_my_team=False, is_captain=False
        """
        raise NotImplementedError
