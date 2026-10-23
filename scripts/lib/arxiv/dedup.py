#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dedup.py — Deduplication component.

Checks candidate papers against existing Zotero library to filter duplicates.
Uses title normalization for matching.

NOT responsible for: arXiv I/O, ranking, Zotero API details.
"""

from __future__ import annotations

import re

from .protocol import Paper


# ─── Public API ───────────────────────────────────────────────────────────────────────────────────────────────────────


class Deduplicator:
    """Filters papers that already exist in the user's library."""

    def __init__(self, library_titles: list[str]):
        """Initialize with existing library titles.

        Args:
            library_titles: List of paper titles from Zotero library.
        """
        self.known_titles_ = {_NormalizeTitle(t) for t in library_titles}

    def Filter(self, candidates: list[Paper]) -> list[Paper]:
        """Remove papers whose titles match the library.

        Args:
            candidates: Papers to check.

        Returns:
            Filtered list (duplicates removed).
        """
        result: list[Paper] = []
        for paper in candidates:
            normalized = _NormalizeTitle(paper.title)
            if normalized not in self.known_titles_:
                result.append(paper)
        return result

    def IsDuplicate(self, paper: Paper) -> bool:
        """Check if a single paper is a duplicate."""
        return _NormalizeTitle(paper.title) in self.known_titles_


# ─── Helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _NormalizeTitle(title: str) -> str:
    """Normalize title for comparison: lowercase, strip punctuation, collapse spaces."""
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title
