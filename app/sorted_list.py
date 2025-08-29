"""A simple sorted list implementation using Python's built-in tools.

This module replaces the external `sortedcontainers` dependency to avoid
known vulnerabilities reported in versions >= 2.4.0.  It provides a minimal
`SortedList` class supporting insertion, removal and iteration while
maintaining order.
"""
from __future__ import annotations

from bisect import bisect_left, insort
from typing import Iterable, Iterator, List, TypeVar

T = TypeVar("T")


class SortedList:
    """Maintain a sorted list without relying on third-party packages."""

    def __init__(self, iterable: Iterable[T] | None = None) -> None:
        self._items: List[T] = sorted(iterable) if iterable is not None else []

    def add(self, value: T) -> None:
        """Insert ``value`` into the list keeping it ordered."""
        insort(self._items, value)

    def remove(self, value: T) -> None:
        """Remove first occurrence of ``value`` or raise ``ValueError``."""
        idx = bisect_left(self._items, value)
        if idx == len(self._items) or self._items[idx] != value:
            raise ValueError(f"{value!r} not in list")
        self._items.pop(idx)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._items)

    def __getitem__(self, index: int) -> T:  # pragma: no cover - trivial
        return self._items[index]

    def __iter__(self) -> Iterator[T]:  # pragma: no cover - trivial
        return iter(self._items)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"SortedList({self._items!r})"
