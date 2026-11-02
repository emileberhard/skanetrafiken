from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skane._models import Place


class SkaneError(Exception):
    pass


class UsageError(SkaneError):
    pass


class UnresolvedPlaceError(UsageError):
    def __init__(self, query: str, candidates: tuple[Place, ...]):
        self.query = query
        self.candidates = candidates
        super().__init__(
            f"Place {query!r} does not match exactly one name; "
            "use a candidate's exact name or id, or coordinates."
        )


class UnavailableError(SkaneError):
    pass


class InvalidResponseError(SkaneError):
    pass
