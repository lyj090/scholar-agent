#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
formatter.py — Output formatting component.

Renders Paper lists as human-readable tables, JSON, or Markdown reports.
NOT responsible for: data retrieval, ranking, I/O.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from .cache import note
from .protocol import Paper

# ─── Constants ────────────────────────────────────────────────────────────────────────────────────────────────

kTitleMaxLen = 60
kAuthorsMaxLen = 30


# ─── Public API ───────────────────────────────────────────────────────────────────────────────────────────────

class Formatter:
    """Renders paper lists in various output formats."""

    def PrintTable(self, papers: list[Paper], show_score: bool = True, recommended_ids: set[str] = None) -> None:
        """Print papers as a formatted table to stdout.

        Args:
            papers: List of Paper objects.
            show_score: Whether to show relevance score.
            recommended_ids: Set of arxiv_ids that were recommended before.
                            If None, auto-load from data/recommended.json.
        """
        if not papers:
            note("No papers found.")
            return

        # Load recommended IDs if not provided
        if recommended_ids is None:
            try:
                from .store import JsonStore
                store = JsonStore("data/recommended.json")
                recommended_ids = store.GetIDs()
            except Exception:
                recommended_ids = set()

        # Header
        if show_score:
            header = f"{'#':>3} {'Score':>5} {'ID':<16} {'Cat':<6} {'Title':<{kTitleMaxLen}} {'Authors'}"
            sep = "-" * (3 + 1 + 5 + 1 + 16 + 1 + 6 + 1 + kTitleMaxLen + 1 + kAuthorsMaxLen)
        else:
            header = f"{'#':>3} {'ID':<16} {'Cat':<6} {'Title':<{kTitleMaxLen}} {'Authors'}"
            sep = "-" * (3 + 1 + 16 + 1 + 6 + 1 + kTitleMaxLen + 1 + kAuthorsMaxLen)

        print(sep)
        print(header)
        print(sep)

        for i, paper in enumerate(papers, 1):
            title = _Truncate(paper.title, kTitleMaxLen)
            authors = _Truncate(", ".join(paper.authors), kAuthorsMaxLen)
            cat = paper.primary_category[:6] if paper.primary_category else ""
            arxiv_id = paper.arxiv_id[:16]

            # Mark recommended papers with ⭐
            if arxiv_id in recommended_ids:
                title = "⭐ " + title

            if show_score:
                print(f"{i:>3} {paper.score:>5.2f} {arxiv_id:<16} {cat:<6} {title:<{kTitleMaxLen}} {authors}")
            else:
                print(f"{i:>3} {arxiv_id:<16} {cat:<6} {title:<{kTitleMaxLen}} {authors}")

        print(sep)
        print(f"Total: {len(papers)} papers")
        if recommended_ids:
            note(f"⭐ = Recommended before (in data/recommended.json)")

    def PrintJson(self, papers: list[Paper]) -> None:
        """Print papers as JSON array to stdout."""
        data = [p.ToDict() for p in papers]
        print(json.dumps(data, ensure_ascii=False, indent=2))

    def PrintSummary(self, papers: list[Paper], label: str = "Results") -> None:
        """Print a brief one-line summary."""
        note(f"[{label}] {len(papers)} papers")

    def FormatReadme(self, papers_by_keyword: dict[str, list[Paper]], config: dict | None = None) -> str:
        """Generate daily-readme format (table with TOC, badges, back-to-top links).

        Args:
            papers_by_keyword: Dict mapping keyword group name to list of Papers.
            config: Optional configuration dict.

        Returns:
            Markdown string for docs/arxiv/daily-readme.md.
        """
        lines = []
        date_str = datetime.now().strftime("%Y-%m-%d")

        # Title
        lines.append("# Robotics arXiv Daily")
        lines.append("")
        lines.append(f"Updated on **{date_str}**")
        lines.append("")

        # TOC
        if papers_by_keyword:
            lines.append(_GenerateTOC(list(papers_by_keyword.keys())))
            lines.append("")

        # Papers by keyword
        for keyword, papers in papers_by_keyword.items():
            if not papers:
                continue
            anchor = keyword.lower().replace(" ", "-").replace("_", "-")
            lines.append(f"## {keyword}")
            lines.append("")
            lines.append("| Publish Date | Title | Authors | PDF | Code |")
            lines.append("|--------------|-------|---------|-----|------|")

            for paper in papers:
                title = pretty_math(paper.title)
                title_link = f"[{title}]({paper.abs_url})"
                authors = _FormatAuthors(paper.authors)
                date = _FormatDate(paper.published)
                pdf_link = f"[pdf]({paper.pdf_url})"
                code_link = f"[code]({paper.code_url})" if paper.code_url else "null"

                lines.append(f"| {date} | {title_link} | {authors} | {pdf_link} | {code_link} |")

            lines.append("")
            lines.append(_BackToTop())
            lines.append("")

        return "\n".join(lines)

    def FormatPages(self, papers_by_keyword: dict[str, list[Paper]], config: dict | None = None) -> str:
        """Generate GitHub Pages format (Jekyll-compatible, simplified table).

        Args:
            papers_by_keyword: Dict mapping keyword group name to list of Papers.
            config: Optional configuration dict.

        Returns:
            Markdown string for docs/arxiv/pages-index.md.
        """
        lines = []
        date_str = datetime.now().strftime("%Y-%m-%d")

        # Jekyll frontmatter
        lines.append("---")
        lines.append("layout: page")
        lines.append(f"title: Robotics arXiv Daily - {date_str}")
        lines.append("---")
        lines.append("")

        # Title
        lines.append(f"# Robotics arXiv Daily - {date_str}")
        lines.append("")

        # Papers by keyword (simplified, no TOC)
        for keyword, papers in papers_by_keyword.items():
            if not papers:
                continue
            lines.append(f"## {keyword}")
            lines.append("")
            lines.append("| Title | Authors | PDF | Code |")
            lines.append("|-------|---------|-----|------|")

            for paper in papers:
                title = pretty_math(paper.title)
                title_link = f"[{title}]({paper.abs_url})"
                authors = _FormatAuthors(paper.authors)
                pdf_link = f"[pdf]({paper.pdf_url})"
                code_link = f"[code]({paper.code_url})" if paper.code_url else ""

                lines.append(f"| {title_link} | {authors} | {pdf_link} | {code_link} |")

            lines.append("")

        return "\n".join(lines)

    def FormatWechat(self, papers_by_keyword: dict[str, list[Paper]], config: dict | None = None) -> str:
        """Generate WeChat format (bullet-point format).

        Args:
            papers_by_keyword: Dict mapping keyword group name to list of Papers.
            config: Optional configuration dict.

        Returns:
            Markdown string for WeChat article.
        """
        lines = []
        date_str = datetime.now().strftime("%Y-%m-%d")

        # Title
        lines.append(f"# Robotics arXiv Daily - {date_str}")
        lines.append("")

        # Papers by keyword (bullet format)
        for keyword, papers in papers_by_keyword.items():
            if not papers:
                continue
            lines.append(f"## {keyword}")
            lines.append("")

            for paper in papers:
                title = pretty_math(paper.title)
                authors = _FormatAuthors(paper.authors, max_authors=5)
                date = _FormatDate(paper.published)

                lines.append(f"### {title}")
                lines.append(f"- **Authors**: {authors}")
                lines.append(f"- **Date**: {date}")
                lines.append(f"- **ArXiv**: [{paper.arxiv_id}]({paper.abs_url})")
                if paper.code_url:
                    lines.append(f"- **Code**: {paper.code_url}")
                lines.append("")

        return "\n".join(lines)

    def PrintReadme(self, papers_by_keyword: dict[str, list[Paper]], config: dict | None = None) -> None:
        """Print README format to stdout."""
        print(self.FormatReadme(papers_by_keyword, config))

    def PrintPages(self, papers_by_keyword: dict[str, list[Paper]], config: dict | None = None) -> None:
        """Print GitHub Pages format to stdout."""
        print(self.FormatPages(papers_by_keyword, config))

    def PrintWechat(self, papers_by_keyword: dict[str, list[Paper]], config: dict | None = None) -> None:
        """Print WeChat format to stdout."""
        print(self.FormatWechat(papers_by_keyword, config))

    def SaveReadme(self, papers_by_keyword: dict[str, list[Paper]], filepath: str, config: dict | None = None) -> bool:
        """Save README format to file.

        Args:
            papers_by_keyword: Dict mapping keyword group name to list of Papers.
            filepath: Path to output file.
            config: Optional configuration dict.

        Returns:
            True if saved successfully.
        """
        content = self.FormatReadme(papers_by_keyword, config)
        return self._SaveToFile(content, filepath)

    def SavePages(self, papers_by_keyword: dict[str, list[Paper]], filepath: str, config: dict | None = None) -> bool:
        """Save GitHub Pages format to file.

        Args:
            papers_by_keyword: Dict mapping keyword group name to list of Papers.
            filepath: Path to output file.
            config: Optional configuration dict.

        Returns:
            True if saved successfully.
        """
        content = self.FormatPages(papers_by_keyword, config)
        return self._SaveToFile(content, filepath)

    def SaveWechat(self, papers_by_keyword: dict[str, list[Paper]], filepath: str, config: dict | None = None) -> bool:
        """Save WeChat format to file.

        Args:
            papers_by_keyword: Dict mapping keyword group name to list of Papers.
            filepath: Path to output file.
            config: Optional configuration dict.

        Returns:
            True if saved successfully.
        """
        content = self.FormatWechat(papers_by_keyword, config)
        return self._SaveToFile(content, filepath)

    @staticmethod
    def _SaveToFile(content: str, filepath: str) -> bool:
        """Save content to file.

        Args:
            content: String content to save.
            filepath: Path to output file.

        Returns:
            True if saved successfully.
        """
        import os
        try:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except IOError as e:
            note(f"[Formatter] Failed to save {filepath}: {e}")
            return False


# ─── Helpers ──────────────────────────────────────────────────────────────────────────────────────────────────


def pretty_math(text: str) -> str:
    """Clean up LaTeX math expressions for Markdown rendering.

    Args:
        text: Text that may contain LaTeX math ($...$).

    Returns:
        Text with cleaned math expressions.
    """
    def _clean_match(match):
        math_content = match.group(0)
        # Remove excessive whitespace inside $ math $
        inner = re.sub(r"\s+", " ", math_content[1:-1]).strip()
        return f"${inner}$"

    # Find all $...$ patterns and clean them
    return re.sub(r"\$.*?\$", _clean_match, text, flags=re.DOTALL)


def _Truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _FormatAuthors(authors: list[str], max_authors: int = 3) -> str:
    """Format author list for display.

    Args:
        authors: List of author names.
        max_authors: Maximum number of authors to show.

    Returns:
        Formatted author string.
    """
    if not authors:
        return "N/A"
    if len(authors) <= max_authors:
        return ", ".join(authors)
    return ", ".join(authors[:max_authors]) + ", et al."


def _FormatDate(published: str) -> str:
    """Format published date for display.

    Args:
        published: ISO 8601 date string.

    Returns:
        Formatted date (YYYY-MM-DD).
    """
    if not published:
        return "N/A"
    # Extract date part from ISO 8601
    return published[:10] if "T" in published else published


def _GenerateTOC(keywords: list[str]) -> str:
    """Generate Table of Contents HTML.

    Args:
        keywords: List of keyword group names.

    Returns:
        HTML string for TOC (GitHub-compatible details/summary).
    """
    if not keywords:
        return ""

    lines = [
        "<details>",
        "  <summary>Table of Contents</summary>",
        "  <ol>",
    ]
    for kw in keywords:
        anchor = kw.lower().replace(" ", "-").replace("_", "-")
        lines.append(f"    <li><a href='#{anchor}'>{kw}</a></li>")
    lines.extend([
        "  </ol>",
        "</details>",
        "",
    ])
    return "\n".join(lines)


def _BackToTop() -> str:
    """Generate back-to-top link.

    Returns:
        Markdown string for back-to-top link.
    """
    return "<p align='right'>(<a href='#top'>back to top</a>)</p>\n"
