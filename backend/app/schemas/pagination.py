"""A bounded page of results, plus the numbers a client needs to paginate.

Every history endpoint returns one of these instead of a bare list, so a caller
can never accidentally treat a truncated page as the whole collection: the
matching ``total`` is always the count of the *unfiltered-by-page* result set.
"""

from typing import Generic, List, TypeVar

from pydantic import BaseModel, Field, computed_field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """One slice of a collection, described well enough to render real controls.

    ``has_more`` is derived rather than stored so it cannot drift from the
    payload it describes.
    """

    items: List[T] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0

    @computed_field
    @property
    def has_more(self) -> bool:
        """True when at least one further row exists past this page."""
        return self.offset + len(self.items) < self.total
