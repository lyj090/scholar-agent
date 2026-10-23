#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
protocol.py — Shared data structures for arxiv module.

Defines the Paper dataclass used across all components (client, ranker, dedup, formatter).
NOT responsible for: I/O, ranking logic, formatting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Paper:
    """Unified data container for an arXiv paper."""

    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    primary_category: str
    published: str  # ISO 8601 format
    pdf_url: str
    abs_url: str
    doi: str | None = None
    score: float = 0.0  # Relevance score (filled by ranker)
    is_new: bool = True  # False = cross-listed
    code_url: str = ""  # Code repository URL (extracted from comment)
    comment: str = ""  # Raw arXiv comment field

    @property
    def text_for_bm25(self) -> str:
        """Text for BM25 indexing (title + abstract)."""
        return f"{self.title} {self.abstract}".strip()

    @property
    def text_for_embedding(self) -> str:
        """Text for embedding (title + abstract)."""
        return f"Title: {self.title}\n\nAbstract: {self.abstract}".strip()

    def ToDict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "categories": self.categories,
            "primary_category": self.primary_category,
            "published": self.published,
            "pdf_url": self.pdf_url,
            "abs_url": self.abs_url,
            "doi": self.doi,
            "score": round(self.score, 4),
            "is_new": self.is_new,
            "code_url": self.code_url,
            "comment": self.comment,
        }
