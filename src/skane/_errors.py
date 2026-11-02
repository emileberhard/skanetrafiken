from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skane._models import Place


class SkaneError(Exception):
    pass


class UsageError(SkaneError):
    pass


class AmbiguousPlaceError(UsageError):
    def __init__(self, query: str, candidates: tuple[Place, ...]):
        self.query = query
        self.candidates = candidates
        super().__init__(f"Place {query!r} does not resolve to exactly one name.")


class UnavailableError(SkaneError):
    pass


class InvalidResponseError(SkaneError):
    pass
