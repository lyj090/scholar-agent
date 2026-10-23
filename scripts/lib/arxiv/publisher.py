#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publisher.py — Publishing logic for arXiv papers.

Handles categorizing papers by keywords, saving to JSON, and generating output files.
"""

from __future__ import annotations

import os

from .client import ArxivClient
from .config import ConfigLoader, LoadConfig
from .formatter import Formatter
from .protocol import Paper
from .store import JsonStore
from arxiv.cache import note


class Publisher:
    """Handles publishing papers to various output formats."""

    def __init__(self, config: ConfigLoader | None = None):
        """Initialize publisher.

        Args:
            config: ConfigLoader instance. If None, loads default config.
        """
        self._config = config if config else LoadConfig()
        self._formatter = Formatter()
        self._client = ArxivClient()

    def Publish(
        self,
        papers: list[Paper] | None = None,
        categories: list[str] | None = None,
        output_dir: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, bool]:
        """Publish papers to output formats.

        Args:
            papers: List of Paper objects. If None, fetches from arXiv.
            categories: arXiv categories to fetch (if papers is None).
            output_dir: Directory to save output files.
            dry_run: If True, don't actually save files.

        Returns:
            Dict mapping output format to success status.
        """
        # Fetch papers if not provided
        if papers is None:
            if not categories:
                categories = ["cs.RO", "cs.CV", "cs.LG"]
            note("[Publisher] Fetching papers from arXiv...")
            papers = self._client.FetchDaily(categories, include_cross=False)
            note(f"[Publisher] Fetched {len(papers)} papers.")

        if not papers:
            note("[Publisher] No papers to publish.")
            return {}

        # Categorize papers by keywords
        note("[Publisher] Categorizing papers by keywords...")
        papers_by_keyword = self._CategorizePapers(papers)

        # Save to JSON store
        results = {}
        if not dry_run:
            results = self._SaveToJson(papers_by_keyword, output_dir)

        # Generate output files
        if not dry_run:
            results.update(self._GenerateOutputFiles(papers_by_keyword, output_dir))
        else:
            note("[Publisher] Dry run - would generate output files:")
            output_settings = self._config.GetOutputSettings()
            for fmt in ["readme", "pages", "wechat"]:
                if output_settings.get("formats", {}).get(fmt, False):
                    note(f"  - {fmt}.md")

        return results

    def _CategorizePapers(self, papers: list[Paper]) -> dict[str, list[Paper]]:
        """Categorize papers by keyword groups.

        Args:
            papers: List of Paper objects.

        Returns:
            Dict mapping keyword group name to list of matching Papers.
        """
        keywords = self._config.GetKeywords()
        papers_by_keyword: dict[str, list[Paper]] = {name: [] for name in keywords}

        for paper in papers:
            paper_text = f"{paper.title} {paper.abstract}".lower()
            for name, filters in keywords.items():
                for kw in filters:
                    if kw.lower() in paper_text:
                        papers_by_keyword[name].append(paper)
                        break  # Only add to first matching category

        return papers_by_keyword

    def _SaveToJson(
        self, papers_by_keyword: dict[str, list[Paper]], output_dir: str | None = None
    ) -> dict[str, bool]:
        """Save papers to JSON files by keyword group.

        Args:
            papers_by_keyword: Dict mapping keyword group to list of Papers.
            output_dir: Directory to save JSON files.

        Returns:
            Dict mapping keyword group to save success status.
        """
        output_settings = self._config.GetOutputSettings()
        json_dir = output_dir or output_settings.get("json_dir", "./data")
        os.makedirs(json_dir, exist_ok=True)

        results = {}
        for name, papers in papers_by_keyword.items():
            if not papers:
                continue
            filename = f"{name.lower().replace(' ', '_')}.json"
            filepath = os.path.join(json_dir, filename)
            store = JsonStore(filepath)
            papers_dict = [p.ToDict() for p in papers]
            success = store.Save(papers_dict, append=True)
            results[name] = success
            if success:
                note(f"[Publisher] Saved {len(papers)} papers to {filepath}")
            else:
                note(f"[Publisher] Failed to save to {filepath}")

        return results

    def _GenerateOutputFiles(
        self, papers_by_keyword: dict[str, list[Paper]], output_dir: str | None = None
    ) -> dict[str, bool]:
        """Generate output files (README, GitHub Pages, WeChat).

        Args:
            papers_by_keyword: Dict mapping keyword group to list of Papers.
            output_dir: Directory to save output files.

        Returns:
            Dict mapping output format to generation success status.
        """
        output_settings = self._config.GetOutputSettings()
        output_dir = output_dir or "."
        results = {}

        # Filter out empty categories
        non_empty = {k: v for k, v in papers_by_keyword.items() if v}

        # Generate README
        if output_settings.get("formats", {}).get("readme", True):
            readme_path = os.path.join(output_dir, output_settings.get("readme_path", "docs/arxiv/daily-readme.md"))
            content = self._formatter.FormatReadme(non_empty, output_settings)
            success = self._SaveFile(content, readme_path)
            results["readme"] = success

        # Generate GitHub Pages
        if output_settings.get("formats", {}).get("github_pages", True):
            pages_path = os.path.join(output_dir, output_settings.get("pages_path", "./docs/arxiv/pages-index.md"))
            content = self._formatter.FormatPages(non_empty, output_settings)
            success = self._SaveFile(content, pages_path)
            results["pages"] = success

        # Generate WeChat
        if output_settings.get("formats", {}).get("wechat", False):
            wechat_path = os.path.join(output_dir, output_settings.get("wechat_path", "./docs/wechat.md"))
            content = self._formatter.FormatWechat(non_empty, output_settings)
            success = self._SaveFile(content, wechat_path)
            results["wechat"] = success

        return results

    def _SaveFile(self, content: str, filepath: str) -> bool:
        """Save content to file.

        Args:
            content: String content to save.
            filepath: Path to output file.

        Returns:
            True if saved successfully.
        """
        try:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            note(f"[Publisher] Saved to {filepath}")
            return True
        except IOError as e:
            note(f"[Publisher] Failed to save {filepath}: {e}")
            return False


def PublishPapers(
    papers: list[Paper] | None = None,
    categories: list[str] | None = None,
    config_path: str | None = None,
    output_dir: str | None = None,
    dry_run: bool = False,
) -> dict[str, bool]:
    """Convenience function to publish papers.

    Args:
        papers: List of Paper objects.
        categories: arXiv categories to fetch.
        config_path: Path to config.yaml.
        output_dir: Directory to save output files.
        dry_run: If True, don't actually save files.

    Returns:
        Dict mapping output format to success status.
    """
    config = LoadConfig(config_path)
    publisher = Publisher(config)
    return publisher.Publish(papers, categories, output_dir, dry_run)
