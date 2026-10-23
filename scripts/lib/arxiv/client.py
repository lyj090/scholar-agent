#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client.py — arXiv data retrieval component.

Handles RSS feed parsing (daily new papers) and API queries (search/get by ID).
Also handles PDF download.

NOT responsible for: ranking, dedup, formatting, Zotero interaction.
"""

from __future__ import annotations

import os
import random
import re
import time
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .protocol import Paper
from arxiv.cache import note

# ─── Constants ────────────────────────────────────────────────────────────────────────────────────────────────────────

kRssBaseUrl = "https://rss.arxiv.org/atom"
kApiBaseUrl = "https://export.arxiv.org/api/query"
kApiDelay = 3.0  # seconds between API calls (arXiv politeness)
kApiTimeout = 30  # seconds
kApiBatchSize = 20  # max IDs per API request
kMaxRetries = 10  # increased from 3 for better rate limit handling
kRateLimitDelay = 30  # base delay for 429 errors

# XML namespaces
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


# ─── Public API ───────────────────────────────────────────────────────────────────────────────────────────────────────


class ArxivClient:
    """Stateless client for arXiv RSS and API."""

    def FetchDaily(self, categories: list[str], include_cross: bool = False) -> list[Paper]:
        """Fetch today's new papers from arXiv RSS feed.

        Args:
            categories: arXiv categories (e.g. ["cs.RO", "cs.CV"]).
            include_cross: Include cross-listed papers (announce_type=cross).

        Returns:
            List of Paper objects with metadata from API.
            Falls back to RSS-only data (no abstract) if API is rate-limited.
        """
        # Step 1: Get paper IDs + titles from RSS
        paper_ids, rss_titles = self._FetchRssIdsAndTitles(categories, include_cross)
        if not paper_ids:
            return []

        # Step 2: Get full metadata from API (in batches)
        try:
            papers = self._FetchMetadataBatch(paper_ids)
            return papers
        except RuntimeError as e:
            # API unavailable (429 or timeout) — fallback to RSS-only
            note(f"[arXiv] API unavailable ({e}), using RSS titles only.")
            return [
                Paper(
                    arxiv_id=pid,
                    title=rss_titles.get(pid, pid),
                    authors=[],
                    abstract="",
                    categories=[categories[0]] if categories else [],
                    primary_category=categories[0] if categories else "",
                    published="",
                    pdf_url=f"https://arxiv.org/pdf/{pid}",
                    abs_url=f"https://arxiv.org/abs/{pid}",
                )
                for pid in paper_ids
            ]

    def Search(self, query: str, max_results: int = 20) -> list[Paper]:
        """Search arXiv by query string.

        Args:
            query: Free-text or structured query (supports arXiv syntax).
            max_results: Maximum number of results.

        Returns:
            List of Paper objects sorted by submission date (newest first).
        """
        params = (
            f"search_query=all:{_UrlEncode(query)}"
            f"&max_results={max_results}"
            f"&sortBy=submittedDate&sortOrder=descending"
        )
        xml_data = self._ApiRequest(params)
        return self._ParseAtomFeed(xml_data)

    def GetById(self, arxiv_ids: list[str]) -> list[Paper]:
        """Fetch specific papers by arXiv ID.

        Args:
            arxiv_ids: List of IDs like ["2505.12345", "2505.12346v2"].

        Returns:
            List of Paper objects.
        """
        return self._FetchMetadataBatch(arxiv_ids)

    def DownloadPdf(self, paper: Paper, output_dir: str) -> str:
        """Download PDF for a paper.

        Args:
            paper: Paper object with valid pdf_url.
            output_dir: Directory to save PDF.

        Returns:
            Absolute path to downloaded file.
        """
        os.makedirs(output_dir, exist_ok=True)
        safe_id = paper.arxiv_id.replace("/", "_").replace(":", "_")
        filename = f"{safe_id}.pdf"
        filepath = os.path.join(output_dir, filename)

        if os.path.exists(filepath):
            return filepath

        import requests

        resp = requests.get(paper.pdf_url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                f.write(chunk)
        return filepath

    # ─── Private Methods ──────────────────────────────────────────────────────────────────────────────────────────────

    def _ExtractCodeLink(self, comment: str | None) -> str:
        """Extract code repository URL from arXiv comment field."""
        if not comment:
            return ""
        url_pattern = r'https?://[^\s<>"]+'
        urls = re.findall(url_pattern, comment)
        if not urls:
            return ""
        code_hosts = ['github.com', 'gitlab.com', 'bitbucket.org', 'huggingface.co']
        for url in urls:
            url_clean = url.rstrip('.,;')
            if any(host in url_clean for host in code_hosts):
                return url_clean
        return urls[0].rstrip('.,;')

    def _FetchRssIds(self, categories: list[str], include_cross: bool) -> list[str]:
        """Parse RSS feed to extract paper IDs."""
        ids, _ = self._FetchRssIdsAndTitles(categories, include_cross)
        return ids

    def _FetchRssIdsAndTitles(
        self, categories: list[str], include_cross: bool
    ) -> tuple[list[str], dict[str, str]]:
        """Parse RSS feed to extract paper IDs and titles.

        Returns:
            (list of arxiv IDs, dict mapping ID → title)
        """
        query = "+".join(categories)
        url = f"{kRssBaseUrl}/{query}"

        xml_data = self._HttpGet(url)
        root = ET.fromstring(xml_data)

        allowed_types = {"new", "cross"} if include_cross else {"new"}
        paper_ids = []
        titles: dict[str, str] = {}

        for entry in root.findall("atom:entry", _NS):
            announce_el = entry.find("arxiv:announce_type", _NS)
            announce_type = announce_el.text.strip() if announce_el is not None else "new"
            if announce_type not in allowed_types:
                continue

            id_el = entry.find("atom:id", _NS)
            title_el = entry.find("atom:title", _NS)
            if id_el is not None:
                raw_id = id_el.text.strip()
                # Format: oai:arXiv.org:2505.12345v1 → 2505.12345v1
                arxiv_id = raw_id.replace("oai:arXiv.org:", "")
                paper_ids.append(arxiv_id)
                if title_el is not None and title_el.text:
                    titles[arxiv_id] = " ".join(title_el.text.strip().split())

        return paper_ids, titles

    def _FetchMetadataBatch(self, paper_ids: list[str]) -> list[Paper]:
        """Fetch full metadata for a list of IDs, batched."""
        all_papers: list[Paper] = []

        for i in range(0, len(paper_ids), kApiBatchSize):
            batch = paper_ids[i : i + kApiBatchSize]
            id_list = ",".join(batch)
            params = f"id_list={id_list}&max_results={len(batch)}"
            xml_data = self._ApiRequest(params)
            papers = self._ParseAtomFeed(xml_data)
            all_papers.extend(papers)

            if i + kApiBatchSize < len(paper_ids):
                time.sleep(kApiDelay)

        return all_papers

    def _ApiRequest(self, params: str) -> str:
        """Make a request to arXiv API with retry and backoff."""
        url = f"{kApiBaseUrl}?{params}"
        for attempt in range(kMaxRetries):
            try:
                return self._HttpGet(url)
            except RuntimeError as e:
                err_str = str(e)
                # 429 = rate limited: exponential backoff with jitter
                if "429" in err_str:
                    if attempt < kMaxRetries - 1:
                        # Exponential backoff: 30s, 60s, 120s, 240s, capped at 300s
                        wait = min(kRateLimitDelay * (2 ** attempt), 300)
                        # Add ±10% jitter to prevent thundering herd
                        jitter = wait * 0.1
                        wait = wait + random.uniform(-jitter, jitter)
                        wait = max(wait, 10)  # minimum 10s wait
                        print(
                            f"[arXiv] Rate limited (429), waiting {wait:.0f}s...",
                            file=__import__("sys").stderr,
                        )
                        time.sleep(wait)
                    else:
                        raise RuntimeError(
                            f"arXiv API rate limited after {kMaxRetries} attempts. "
                            f"Wait a few minutes and retry."
                        )
                else:
                    # Timeout or other error: exponential backoff
                    if attempt < kMaxRetries - 1:
                        wait = kApiDelay * (2**attempt)  # 3s, 6s, 12s
                        time.sleep(wait)
                    else:
                        raise RuntimeError(f"arXiv API failed after {kMaxRetries} attempts: {e}")
        return ""  # unreachable

    def _ParseAtomFeed(self, xml_data: str) -> list[Paper]:
        """Parse arXiv Atom XML response into Paper objects."""
        root = ET.fromstring(xml_data)
        papers: list[Paper] = []

        for entry in root.findall("atom:entry", _NS):
            paper = self._ParseEntry(entry)
            if paper:
                papers.append(paper)

        return papers

    def _ParseEntry(self, entry: ET.Element) -> Paper | None:
        """Parse a single Atom entry into a Paper."""
        id_el = entry.find("atom:id", _NS)
        title_el = entry.find("atom:title", _NS)
        summary_el = entry.find("atom:summary", _NS)
        published_el = entry.find("atom:published", _NS)

        if id_el is None or title_el is None:
            return None

        abs_url = id_el.text.strip()
        # Extract arxiv_id: http://arxiv.org/abs/2505.12345v1 → 2505.12345v1
        arxiv_id = abs_url.split("/abs/")[-1] if "/abs/" in abs_url else abs_url

        title = _CleanText(title_el.text or "")
        abstract = _CleanText(summary_el.text or "") if summary_el is not None else ""
        published = published_el.text.strip() if published_el is not None else ""

        # Authors
        authors = []
        for author_el in entry.findall("atom:author", _NS):
            name_el = author_el.find("atom:name", _NS)
            if name_el is not None:
                authors.append(name_el.text.strip())

        # Categories
        categories = []
        primary_category = ""
        for cat_el in entry.findall("atom:category", _NS):
            term = cat_el.get("term", "")
            if term:
                categories.append(term)
        primary_el = entry.find("arxiv:primary_category", _NS)
        if primary_el is not None:
            primary_category = primary_el.get("term", "")

        # PDF link
        pdf_url = ""
        for link_el in entry.findall("atom:link", _NS):
            if link_el.get("title") == "pdf":
                pdf_url = link_el.get("href", "")
                break
        if not pdf_url:
            pdf_url = abs_url.replace("/abs/", "/pdf/")

        # DOI
        doi_el = entry.find("arxiv:doi", _NS)
        doi = doi_el.text.strip() if doi_el is not None else None

        # Comment (may contain code links)
        comment_el = entry.find("arxiv:comment", _NS)
        comment = comment_el.text.strip() if comment_el is not None else ""
        code_url = self._ExtractCodeLink(comment)

        return Paper(
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            abstract=abstract,
            categories=categories,
            primary_category=primary_category,
            published=published,
            pdf_url=pdf_url,
            abs_url=abs_url,
            doi=doi,
            comment=comment,
            code_url=code_url,
        )

    def _HttpGet(self, url: str) -> str:
        """Simple HTTP GET returning response body as string."""
        req = Request(url, headers={"User-Agent": "arxiv-fetch/1.0 (scholar-agent)"})
        try:
            with urlopen(req, timeout=kApiTimeout) as resp:
                return resp.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"HTTP request failed: {url} — {e}")


# ─── Helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _CleanText(text: str) -> str:
    """Normalize whitespace in XML text content."""
    return re.sub(r"\s+", " ", text).strip()


def _UrlEncode(text: str) -> str:
    """Minimal URL encoding for query params."""
    from urllib.parse import quote

    return quote(text, safe="")
