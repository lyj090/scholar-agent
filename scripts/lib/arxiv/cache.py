#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cache.py — Zotero corpus cache for arxiv ranking and dedup.

Persists library titles + abstracts to a local JSON file. Supports incremental
updates via Zotero's `since=<version>` API parameter.

Cache file: ~/Zotero/.arxiv_corpus_cache.json (next to MinerU cache dir).

NOT responsible for: arXiv I/O, ranking logic, output formatting.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import re
from collections import Counter

# ─── Progress output ────────────────────────────────────────────────────────
# Agents parse stdout, so progress chatter must be suppressible. Enabled by
# --quiet (global or subcommand) or ARXIV_QUIET=1 for scripted runs.
QUIET = os.environ.get("ARXIV_QUIET", "").strip() in {"1", "true", "yes"}


def set_quiet(value: bool) -> None:
    """Enable progress suppression.

    Sticky-on: both the global and the subcommand `--quiet` feed this, and
    argparse only fills the subcommand slot when the flag follows it. OR-ing
    stops the later call from clearing an earlier True.
    """
    global QUIET
    QUIET = QUIET or bool(value)


def reset_quiet(value: bool = False) -> None:
    """Set QUIET to an exact value (tests and one-shot runs)."""
    global QUIET
    QUIET = bool(value)


def note(message: str) -> None:
    """Print a progress line to stderr unless quiet mode is on."""
    if QUIET:
        return
    print(message, file=sys.stderr)

# ─── Constants ────────────────────────────────────────────────────────────────────────────────────────────────────────

kCacheFilename = ".arxiv_corpus_cache.json"
kCacheVersion = 1
kMaxCorpusSize = 500  # Keep at most N items in cache
kProfileTopKeywords = 20  # Number of keywords in user profile
kProfileTopCategories = 5

# Stop words for profile keyword extraction
_kStopWords = frozenset({
    "with", "that", "this", "from", "have", "been", "also", "which", "their",
    "more", "than", "other", "these", "such", "using", "based", "into", "over",
    "between", "each", "both", "some", "only", "most", "first", "then", "where",
    "when", "while", "will", "about", "after", "paper", "method", "methods",
    "approach", "proposed", "results", "show", "propose", "present", "novel",
    "state", "model", "models", "system", "systems", "data", "work", "achieve",
    "demonstrate", "performance", "existing", "different", "used", "make",
    "without", "well", "however", "provide", "compared", "able", "allows",
    "framework", "problem", "number", "order", "case", "high", "under",
    "through", "across", "large", "given", "enable", "simple", "often",
    "previous", "recent", "several", "many", "various", "effective", "directly",
    "preprint", "arxiv", "abstract", "introduction", "conclusion", "figure",
    "table", "equation", "section", "chapter", "reference", "appendix",
    "mineru", "cache", "zotero", "zhelper", "plugin", "subgoal",
})

# Stop words specifically for bigram filtering
_kBigramStopParts = frozenset({
    "the", "and", "for", "our", "its", "are", "was", "can", "has", "had",
    "not", "but", "all", "any", "may", "new", "use", "two", "one", "per",
    "via", "pdf", "llm", "pre", "non", "cache", "mineru", "zotero",
    "zhelper", "search", "plugin", "ying", "ball", "level",
})


# ─── Data Structures ──────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class CorpusEntry:
    """A single cached library item."""

    key: str
    title: str
    abstract: str
    added: str  # ISO 8601 dateAdded
    tags: list[str] = None  # Zotero tags (e.g. ["area/robotics", "topic/manipulation"])

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


# ─── Public API ───────────────────────────────────────────────────────────────────────────────────────────────────────


class CorpusCache:
    """Manages local cache of Zotero library corpus for ranking/dedup."""

    def __init__(self, cache_dir: str):
        """Initialize cache.

        Args:
            cache_dir: Directory to store cache file (typically Zotero data dir).
        """
        self.cache_path_ = os.path.join(os.path.dirname(cache_dir.rstrip("/")), kCacheFilename)
        self.entries_: list[CorpusEntry] = []
        self.last_version_: int = 0
        self.updated_at_: str = ""
        self.loaded_ = False

    def Load(self) -> bool:
        """Load cache from disk. Returns True if valid cache exists."""
        if not os.path.isfile(self.cache_path_):
            return False

        try:
            with open(self.cache_path_, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False

        if data.get("version") != kCacheVersion:
            return False

        self.last_version_ = data.get("last_item_version", 0)
        self.updated_at_ = data.get("updated_at", "")
        self.entries_ = [
            CorpusEntry(
                key=e["key"],
                title=e["title"],
                abstract=e["abstract"],
                added=e["added"],
                tags=e.get("tags", []),
            )
            for e in data.get("corpus", [])
        ]
        self.loaded_ = True
        return True

    def Update(self, api_url: str, user_id: str) -> int:
        """Incremental update: fetch only items added/modified since last sync.

        Args:
            api_url: Zotero Local API base URL.
            user_id: Zotero user ID.

        Returns:
            Number of new/updated entries, or -1 if Zotero unreachable.
        """
        from zotero.api import ZoteroAPI

        api = ZoteroAPI(api_url, user_id)
        if not api.check_connection():
            return -1

        # Get library version header to compare
        if self.last_version_ > 0:
            # Fetch only items modified since our last sync
            items = self._FetchSince(api, self.last_version_)
        else:
            # Full fetch (first time or after rebuild)
            items = api.get_items(limit=kMaxCorpusSize, sort="dateAdded", direction="desc")

        if items is None:
            return -1

        # Merge into cache
        n_updated = self._MergeItems(items)

        # Update metadata
        self.updated_at_ = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Use the max version from fetched items (or keep current)
        for item in items:
            v = item.get("version", 0)
            if v > self.last_version_:
                self.last_version_ = v

        self._Save()
        self.loaded_ = True
        return n_updated

    def Rebuild(self, api_url: str, user_id: str) -> int:
        """Full rebuild: discard cache and re-fetch everything.

        Returns:
            Number of entries in rebuilt cache, or -1 if unreachable.
        """
        self.entries_ = []
        self.last_version_ = 0
        return self.Update(api_url, user_id)

    def GetTitles(self) -> list[str]:
        """Return all cached titles (for dedup)."""
        return [e.title for e in self.entries_]

    def GetAbstracts(self) -> list[str]:
        """Return abstracts ordered by dateAdded desc (for ranking).

        Only returns non-empty abstracts (>50 chars).
        """
        return [e.abstract for e in self.entries_ if len(e.abstract) > 50]

    def IsLoaded(self) -> bool:
        """Whether cache has been loaded (from disk or API)."""
        return self.loaded_

    def Size(self) -> int:
        """Number of entries in cache."""
        return len(self.entries_)

    def Age(self) -> str:
        """Human-readable age of cache (e.g. '2h ago', '3d ago')."""
        if not self.updated_at_:
            return "never"
        try:
            updated = datetime.fromisoformat(self.updated_at_)
            delta = datetime.now(timezone.utc) - updated
            seconds = int(delta.total_seconds())
            if seconds < 60:
                return f"{seconds}s ago"
            elif seconds < 3600:
                return f"{seconds // 60}m ago"
            elif seconds < 86400:
                return f"{seconds // 3600}h ago"
            else:
                return f"{seconds // 86400}d ago"
        except (ValueError, TypeError):
            return "unknown"

    def GetProfile(self) -> dict:
        """Get user research profile (keywords + categories extracted from library).

        Returns dict with:
            keywords: list[str] — top research keywords
            categories: list[str] — likely arXiv categories
        """
        if not self.entries_:
            return {"keywords": [], "categories": []}
        return self._BuildProfile()

    def GetProfileKeywords(self) -> list[str]:
        """Get user's top research keywords for search enrichment."""
        return self.GetProfile().get("keywords", [])

    def GetProfileCategories(self) -> list[str]:
        """Get user's likely arXiv categories."""
        return self.GetProfile().get("categories", ["cs.RO"])

    # ─── Private Methods ──────────────────────────────────────────────────────────────────────────────────────────────

    def _FetchSince(self, api, since_version: int) -> list[dict] | None:
        """Fetch items modified since a given library version."""
        try:
            from urllib.request import Request, urlopen
            from urllib.parse import urlencode
            import json as json_mod

            base = f"{api._base}/items/top"
            params = {
                "since": str(since_version),
                "limit": "100",
                "sort": "dateAdded",
                "direction": "desc",
                "format": "json",
                "itemType": "-attachment && -note",
            }
            url = f"{base}?{urlencode(params)}"
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=10) as resp:
                return json_mod.loads(resp.read().decode("utf-8"))
        except Exception as e:
            note(f"[cache] Incremental fetch failed: {e}")
            return None

    def _MergeItems(self, items: list[dict]) -> int:
        """Merge fetched items into cache. Returns count of new/updated."""
        existing_keys = {e.key: i for i, e in enumerate(self.entries_)}
        n_new = 0

        for item in items:
            data = item.get("data", {})
            key = data.get("key", "")
            title = data.get("title", "").strip()
            abstract = data.get("abstractNote", "").strip()
            added = data.get("dateAdded", "")
            tags = [t["tag"] for t in data.get("tags", []) if t.get("tag")]

            if not key or not title:
                continue

            entry = CorpusEntry(key=key, title=title, abstract=abstract, added=added, tags=tags)

            if key in existing_keys:
                # Update existing
                self.entries_[existing_keys[key]] = entry
            else:
                # Insert new (at front, since sorted by dateAdded desc)
                self.entries_.insert(0, entry)
                n_new += 1

        # Trim to max size
        if len(self.entries_) > kMaxCorpusSize:
            self.entries_ = self.entries_[:kMaxCorpusSize]

        return n_new

    def _BuildProfile(self) -> dict:
        """Extract user research profile from cached corpus.

        Uses two sources (priority order):
        1. Zotero tags (structured, curated by user) — most reliable
        2. Title + abstract keywords (fallback when tags sparse)

        Analyzes ALL entries for tag profile, recent 60 for keyword trends.
        """
        if not self.entries_:
            return {"keywords": [], "categories": ["cs.RO"], "topics": []}

        # ─── Source 1: Tags (high quality, user-curated) ────────────────
        all_tags: Counter = Counter()
        for e in self.entries_:
            for tag in e.tags:
                # Only count structured tags (with namespace prefix)
                if "/" in tag:
                    all_tags[tag] += 1

        # Extract topic keywords from tags (e.g. "topic/manipulation" → "manipulation")
        tag_topics = []
        for tag, count in all_tags.most_common(30):
            parts = tag.split("/", 1)
            if len(parts) == 2 and parts[0] in ("topic", "area", "method", "task"):
                tag_topics.append(parts[1].replace("-", " "))

        # ─── Source 2: Text keywords (supplement) ───────────────────────
        recent = self.entries_[:60]
        all_text = " ".join(
            f"{e.title} {e.abstract}" for e in recent if e.title
        ).lower()

        # Bigrams
        bigrams = re.findall(r"([a-z]{3,}[\s-][a-z]{3,})", all_text)
        bigrams = [
            bg for bg in bigrams
            if not any(p in _kBigramStopParts for p in bg.replace("-", " ").split())
            and not any(p in _kStopWords for p in bg.replace("-", " ").split())
        ]
        bigram_freq = Counter(bigrams)

        # Unigrams
        unigrams = re.findall(r"[a-z]{4,}", all_text)
        unigrams = [w for w in unigrams if w not in _kStopWords]
        unigram_freq = Counter(unigrams)

        # Combine: tags first, then text keywords
        keywords = list(tag_topics[:10])  # Tag-derived topics (highest priority)
        # Add text-derived keywords that aren't already covered
        seen = set(keywords)
        for bg, cnt in bigram_freq.most_common(30):
            if cnt >= 3 and bg not in seen:
                keywords.append(bg)
                seen.add(bg)
            if len(keywords) >= kProfileTopKeywords:
                break
        for ug, cnt in unigram_freq.most_common(50):
            if cnt >= 5 and ug not in seen:
                keywords.append(ug)
                seen.add(ug)
            if len(keywords) >= kProfileTopKeywords:
                break

        # Infer categories
        categories = self._InferCategories(keywords)

        return {"keywords": keywords, "categories": categories, "topics": tag_topics}

    def _InferCategories(self, keywords: list[str]) -> list[str]:
        """Infer arXiv categories from extracted keywords."""
        # Simple rule-based mapping
        category_signals = {
            "cs.RO": ["robot", "manipulation", "grasp", "locomotion", "humanoid",
                      "mobile robot", "planning", "control", "actuator"],
            "cs.CV": ["image", "visual", "detection", "segmentation", "camera",
                      "event camera", "tracking", "vision", "perception"],
            "cs.LG": ["learning", "reinforcement", "policy", "training",
                      "neural", "optimization", "generalization"],
            "cs.AI": ["reasoning", "language", "agent", "foundation model",
                      "transformer", "representation"],
        }

        scores: Counter = Counter()
        kw_set = set(keywords)
        kw_text = " ".join(keywords)

        for cat, signals in category_signals.items():
            for signal in signals:
                if signal in kw_text:
                    scores[cat] += 1

        if not scores:
            return ["cs.RO"]

        return [cat for cat, _ in scores.most_common(kProfileTopCategories)]

    def _Save(self) -> None:
        """Persist cache to disk."""
        data = {
            "version": kCacheVersion,
            "updated_at": self.updated_at_,
            "last_item_version": self.last_version_,
            "profile": self._BuildProfile(),
            "corpus": [
                {
                    "key": e.key, "title": e.title, "abstract": e.abstract,
                    "added": e.added, "tags": e.tags,
                }
                for e in self.entries_
            ],
        }
        try:
            os.makedirs(os.path.dirname(self.cache_path_), exist_ok=True)
            with open(self.cache_path_, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        except OSError as e:
            note(f"[cache] Save failed: {e}")
