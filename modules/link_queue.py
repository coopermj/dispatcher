#!/usr/bin/env python3
"""BFS worklist for multi-level link following: dedup, depth, and budget."""

from dataclasses import dataclass
from typing import Optional


def normalize_url(url):
    """Strip query string, fragment, and trailing slash for comparison."""
    return url.split('?')[0].split('#')[0].rstrip('/')


@dataclass
class LinkTarget:
    url: str
    depth: int
    link_text: str = ''
    parent_url: Optional[str] = None


class LinkQueue:
    """FIFO worklist for BFS link crawling.

    add() enforces dedup (by normalized URL), the max depth, and a global
    budget on total accepted entries — the budget is what guarantees the
    final PDF can't explode at depth 2+. pop_level() drains all pending
    entries at the shallowest depth, so level-1 links are always processed
    (and consume budget) before any level-2 link.
    """

    def __init__(self, max_depth, budget, exclude_urls=()):
        self.max_depth = max_depth
        self.budget = budget
        self.accepted = 0
        self._seen = {normalize_url(u) for u in exclude_urls}
        self._pending = []

    def add(self, url, depth, link_text='', parent_url=None):
        norm = normalize_url(url)
        if depth > self.max_depth:
            return False
        if self.accepted >= self.budget:
            return False
        if norm in self._seen:
            return False
        self._seen.add(norm)
        self.accepted += 1
        self._pending.append(LinkTarget(url, depth, link_text, parent_url))
        return True

    def pending(self):
        return bool(self._pending)

    def pop_level(self):
        if not self._pending:
            return []
        depth = min(t.depth for t in self._pending)
        level = [t for t in self._pending if t.depth == depth]
        self._pending = [t for t in self._pending if t.depth != depth]
        return level
