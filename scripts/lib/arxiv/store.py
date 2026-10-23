#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py — JSON persistence layer for paper data.

Handles saving/loading papers to/from JSON files with deduplication.
Supports incremental updates and multiple output formats.
"""

from __future__ import annotations

import json
import os


class JsonStore:
    """JSON file storage with deduplication by arxiv_id."""

    def __init__(self, filepath: str):
        """Initialize store with file path.

        Args:
            filepath: Path to JSON file for storing papers.
        """
        self._filepath = filepath
        self._papers: list[dict] = []

    def Load(self) -> list[dict]:
        """Load papers from JSON file.

        Returns:
            List of paper dicts. Empty list if file doesn't exist or is invalid.
        """
        if not os.path.exists(self._filepath):
            self._papers = []
            return []

        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._papers = data
            else:
                self._papers = []
            return self._papers
        except (json.JSONDecodeError, IOError) as e:
            print(f"[JsonStore] Failed to load {self._filepath}: {e}")
            self._papers = []
            return []

    def Save(self, papers: list[dict] | None = None, append: bool = False) -> bool:
        """Save papers to JSON file.

        Args:
            papers: List of paper dicts to save. If None, saves self._papers.
            append: If True, merge with existing papers (dedup by arxiv_id).

        Returns:
            True if saved successfully, False otherwise.
        """
        if papers is None:
            papers = self._papers
        elif append:
            existing = self.Load()
            papers = self.Merge(existing, papers)

        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(self._filepath)), exist_ok=True)

        try:
            with open(self._filepath, "w", encoding="utf-8") as f:
                json.dump(papers, f, ensure_ascii=False, indent=2)
            self._papers = papers
            return True
        except IOError as e:
            print(f"[JsonStore] Failed to save {self._filepath}: {e}")
            return False

    def Merge(self, existing: list[dict], new: list[dict]) -> list[dict]:
        """Merge new papers with existing, deduplicating by arxiv_id.

        Args:
            existing: List of existing paper dicts.
            new: List of new paper dicts.

        Returns:
            Merged list with duplicates removed (new papers take precedence).
        """
        # Build lookup by arxiv_id
        lookup: dict[str, dict] = {}

        # Add existing papers to lookup
        for paper in existing:
            arxiv_id = paper.get("arxiv_id", "")
            if arxiv_id:
                lookup[arxiv_id] = paper

        # Add/update with new papers
        for paper in new:
            arxiv_id = paper.get("arxiv_id", "")
            if arxiv_id:
                lookup[arxiv_id] = paper  # New paper overwrites existing

        return list(lookup.values())

    def GetPapers(self) -> list[dict]:
        """Get currently loaded papers."""
        return self._papers

    def Clear(self) -> None:
        """Clear all papers from memory (doesn't delete file)."""
        self._papers = []

    def RemovePapers(self, arxiv_ids: list[str]) -> bool:
        """Remove papers by arxiv_id.

        Args:
            arxiv_ids: List of arxiv_id strings to remove.

        Returns:
            True if save successful, False otherwise.
        """
        ids_to_remove = set(arxiv_ids)
        self._papers = [p for p in self._papers if p.get("arxiv_id") not in ids_to_remove]
        return self.Save()

    def GetIDs(self) -> set[str]:
        """Get all arxiv_ids in the store.

        Returns:
            Set of arxiv_id strings.
        """
        return {p.get("arxiv_id") for p in self._papers if p.get("arxiv_id")}

    def HasID(self, arxiv_id: str) -> bool:
        """Check if a paper with given arxiv_id exists.

        Args:
            arxiv_id: arXiv ID to check.

        Returns:
            True if paper exists, False otherwise.
        """
        return any(p.get("arxiv_id") == arxiv_id for p in self._papers)


def SavePapers(papers: list[dict], filepath: str, append: bool = False) -> bool:
    """Convenience function to save papers to JSON file.

    Args:
        papers: List of paper dicts.
        filepath: Path to JSON file.
        append: If True, merge with existing papers.

    Returns:
        True if saved successfully.
    """
    store = JsonStore(filepath)
    return store.Save(papers, append=append)


def LoadPapers(filepath: str) -> list[dict]:
    """Convenience function to load papers from JSON file.

    Args:
        filepath: Path to JSON file.

    Returns:
        List of paper dicts.
    """
    store = JsonStore(filepath)
    return store.Load()


def MergePapers(existing_path: str, new_papers: list[dict]) -> list[dict]:
    """Convenience function to merge new papers with existing JSON file.

    Args:
        existing_path: Path to existing JSON file.
        new_papers: List of new paper dicts.

    Returns:
        Merged list of paper dicts.
    """
    store = JsonStore(existing_path)
    existing = store.Load()
    return store.Merge(existing, new_papers)


# ─── Multi-Format JSON Storage ──────────────────────────────────────────


class MultiFormatStore:
    """Manages multiple JSON files for different output formats."""

    def __init__(self, base_dir: str):
        """Initialize with base directory for JSON files.

        Args:
            base_dir: Directory where JSON files will be stored.
        """
        self._base_dir = base_dir
        self._stores: dict[str, JsonStore] = {}

    def GetStore(self, format_name: str) -> JsonStore:
        """Get or create a JsonStore for a format.

        Args:
            format_name: Name of format (e.g., "readme", "pages", "wechat").

        Returns:
            JsonStore instance for that format.
        """
        if format_name not in self._stores:
            filepath = os.path.join(self._base_dir, f"{format_name}.json")
            self._stores[format_name] = JsonStore(filepath)
        return self._stores[format_name]

    def SaveAll(self, papers_by_format: dict[str, list[dict]]) -> dict[str, bool]:
        """Save papers for multiple formats.

        Args:
            papers_by_format: Dict mapping format name to list of paper dicts.

        Returns:
            Dict mapping format name to success status.
        """
        results = {}
        for format_name, papers in papers_by_format.items():
            store = self.GetStore(format_name)
            results[format_name] = store.Save(papers)
        return results

    def LoadAll(self) -> dict[str, list[dict]]:
        """Load papers from all format JSON files.

        Returns:
            Dict mapping format name to list of paper dicts.
        """
        results = {}
        if not os.path.exists(self._base_dir):
            return results

        for filename in os.listdir(self._base_dir):
            if filename.endswith(".json"):
                format_name = filename[:-5]  # Remove .json
                store = self.GetStore(format_name)
                results[format_name] = store.Load()
        return results
