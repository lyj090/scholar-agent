#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reliable, token-efficient catalog for LLM-for-Zotero MinerU caches.

The catalog treats ``_llm_source.json`` as the authoritative mapping between a
numeric MinerU cache directory and Zotero attachment/parent keys.  Markdown
title matching is retained only as a compatibility fallback for old caches.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

from .identifiers import PaperIdentifiers, extract_identifiers

if TYPE_CHECKING:
    from .api import ZoteroAPI
    from .config import ZoteroConfig
    from .library_index import LibraryIndex


SOURCE_FILENAME = "_llm_source.json"
FULL_MD_FILENAME = "full.md"
MANIFEST_FILENAME = "manifest.json"


class MineruCatalogError(Exception):
    """Base class for catalog errors."""


class MineruNotFoundError(MineruCatalogError):
    """Raised when no cache matches a reference."""


class MineruAmbiguousError(MineruCatalogError):
    """Raised when a parent item or text query matches multiple caches."""

    def __init__(self, reference: str, candidates: Iterable["MineruRecord"]):
        self.reference = reference
        self.candidates = list(candidates)
        labels = ", ".join(
            f"{r.cache_id}:{r.title or r.source_filename or r.attachment_key}"
            for r in self.candidates[:8]
        )
        super().__init__(
            f"Ambiguous MinerU reference {reference!r}; "
            f"use an attachment key, cache ID, or --match. Candidates: {labels}"
        )


@dataclass(frozen=True)
class MineruSection:
    """A Markdown heading and its subtree span."""

    index: int
    level: int
    heading: str
    char_start: int
    self_char_end: int
    char_end: int

    @property
    def chars(self) -> int:
        return self.char_end - self.char_start

    @property
    def self_chars(self) -> int:
        return self.self_char_end - self.char_start

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "level": self.level,
            "heading": self.heading,
            "char_start": self.char_start,
            "self_char_end": self.self_char_end,
            "char_end": self.char_end,
            "self_chars": self.self_chars,
            "chars": self.chars,
        }


@dataclass(frozen=True)
class MineruRecord:
    """One usable MinerU cache directory."""

    cache_id: int
    cache_dir: str
    full_md_path: str
    manifest_path: str | None
    attachment_key: str | None
    parent_item_key: str | None
    source_filename: str | None
    title: str
    origin: str | None
    parsed_at: str | None
    source_version: int | None

    @property
    def zotero_pdf_url(self) -> str | None:
        if not self.attachment_key:
            return None
        return f"zotero://open-pdf/library/items/{self.attachment_key}"

    @property
    def zotero_item_url(self) -> str | None:
        key = self.parent_item_key or self.attachment_key
        if not key:
            return None
        return f"zotero://select/library/items/{key}"

    def to_dict(self, *, has_note: bool | None = None) -> dict:
        data = {
            "cache_id": self.cache_id,
            "title": self.title,
            "attachment_key": self.attachment_key,
            "parent_item_key": self.parent_item_key,
            "source_filename": self.source_filename,
            "cache_path": self.cache_dir,
            "manifest_path": self.manifest_path,
            "parsed_at": self.parsed_at,
            "origin": self.origin,
            "zotero_pdf_url": self.zotero_pdf_url,
            "zotero_item_url": self.zotero_item_url,
        }
        if has_note is not None:
            data["has_note"] = has_note
        return data


@dataclass(frozen=True)
class MineruReadResult:
    """Bounded content returned for one cache or section."""

    record: MineruRecord
    content: str
    section: MineruSection | None
    truncated: bool
    original_chars: int

    def to_dict(self) -> dict:
        return {
            "source": self.record.to_dict(),
            "section": self.section.to_dict() if self.section else None,
            "truncated": self.truncated,
            "original_chars": self.original_chars,
            "returned_chars": len(self.content),
            "content": self.content,
        }


@dataclass(frozen=True)
class MineruGrepHit:
    """One excerpt matched inside a cached paper."""

    excerpt: str
    char_offset: int

    def to_dict(self) -> dict:
        return {"excerpt": self.excerpt, "char_offset": self.char_offset}


@dataclass(frozen=True)
class MineruGrepMatch:
    """All hits for one paper, plus enough context to open it."""

    record: MineruRecord
    hits: tuple["MineruGrepHit", ...]
    total_hits: int

    def to_dict(self) -> dict:
        return {
            "cache_id": self.record.cache_id,
            "title": self.record.title,
            "attachment_key": self.record.attachment_key,
            "cache_path": self.record.cache_dir,
            "total_hits": self.total_hits,
            "hits": [hit.to_dict() for hit in self.hits],
        }


class MineruNoteIndex:
    """Index Obsidian notes by exact Zotero keys and normalized filenames."""

    _KEY_PATTERNS = (
        re.compile(r"(?mi)^zotero_pdf_key:\s*[\"']?([A-Z0-9]{8})"),
        re.compile(r"(?mi)^zotero_key:\s*[\"']?([A-Z0-9]{8})"),
        re.compile(r"zotero://(?:open-pdf|select)/library/items/([A-Z0-9]{8})"),
    )

    def __init__(self, vault_dir: str):
        self._keys: set[str] = set()
        self._stems: set[str] = set()
        self._scan(vault_dir)

    def _scan(self, vault_dir: str) -> None:
        root = Path(vault_dir)
        if not root.is_dir():
            return
        for path in root.rglob("*.md"):
            self._stems.add(_normalize_text(path.stem))
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pattern in self._KEY_PATTERNS:
                self._keys.update(m.group(1).upper() for m in pattern.finditer(text))

    def has_note(self, record: MineruRecord) -> bool:
        exact_keys = {record.attachment_key, record.parent_item_key} - {None}
        if any(key.upper() in self._keys for key in exact_keys):
            return True

        candidates = {
            _normalize_text(record.title),
            _normalize_text(Path(record.source_filename).stem)
            if record.source_filename
            else "",
        } - {""}
        return any(
            len(candidate) >= 12
            and any(candidate in stem or stem in candidate for stem in self._stems)
            for candidate in candidates
        )


class MineruCatalog:
    """Scan, resolve, outline, and read MinerU caches without loading PDFs."""

    def __init__(
        self,
        cache_dir: str,
        api: "ZoteroAPI | None" = None,
        config: "ZoteroConfig | None" = None,
    ):
        self.cache_dir = os.path.abspath(os.path.expanduser(cache_dir))
        self.api = api
        self._config = config
        self._records: list[MineruRecord] | None = None
        self._api_items: dict[str, dict | None] = {}
        # None = not built yet, False = unavailable (API error / no config).
        self._library_index: "LibraryIndex | None | bool" = None
        # Lazily loaded key -> storageHash map for offline MD5 comparison.
        self._storage_hashes: dict[str, str] | None = None

    def records(self) -> list[MineruRecord]:
        if self._records is None:
            self._records = self._scan()
        return list(self._records)

    def find(self, query: str | None = None) -> list[MineruRecord]:
        records = self.records()
        if not query:
            return records
        needle = _normalize_text(query)
        return [r for r in records if self._matches_text(r, needle)]

    def search_content(
        self,
        pattern: str,
        *,
        regex: bool = False,
        case_sensitive: bool = False,
        context: int = 0,
        max_papers: int = 5,
        hits_per_paper: int = 1,
    ) -> list[MineruGrepMatch]:
        """Search the full text of every local cache, offline.

        This is the local answer to "do I already have a paper about X?" — the
        question agents currently answer with ``web_search``. A full scan of the
        corpus is a few milliseconds because it is plain file reads.
        """
        if not pattern:
            return []
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern if regex else re.escape(pattern), flags)
        except re.error as exc:
            raise MineruCatalogError(f"Invalid regex {pattern!r}: {exc}") from exc

        matches: list[MineruGrepMatch] = []
        for record in self.records():
            try:
                text = self.read_full(record)
            except MineruNotFoundError:
                continue
            if not compiled.search(text):
                continue

            hits: list[MineruGrepHit] = []
            total = 0
            for found in compiled.finditer(text):
                total += 1
                if len(hits) >= max(1, hits_per_paper):
                    continue
                start = max(0, found.start() - context)
                end = min(len(text), found.end() + context)
                excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
                hits.append(MineruGrepHit(excerpt=excerpt, char_offset=found.start()))
            matches.append(
                MineruGrepMatch(record=record, hits=tuple(hits), total_hits=total)
            )
            if len(matches) >= max(1, max_papers):
                break
        return matches

    def logical_groups(
        self, records: Iterable[MineruRecord] | None = None
    ) -> list[list[MineruRecord]]:
        """Group duplicate caches that point to the same underlying PDF.

        Candidate duplicates are first identified by normalized source filename
        and title. They are collapsed only when Zotero confirms the same PDF
        MD5, so generic filenames such as ``paper.pdf`` do not merge unrelated
        papers.
        """
        source_records = list(records) if records is not None else self.records()
        preliminary: dict[tuple[str, str], list[MineruRecord]] = {}
        for record in source_records:
            key = (
                _normalize_text(record.source_filename or ""),
                _normalize_text(record.title),
            )
            preliminary.setdefault(key, []).append(record)

        groups: list[list[MineruRecord]] = []
        for candidates in preliminary.values():
            if len(candidates) == 1 or self.api is None:
                groups.extend([[record] for record in candidates])
                continue

            by_md5: dict[str, list[MineruRecord]] = {}
            for record in candidates:
                md5 = self._attachment_md5(record)
                # Missing hashes remain isolated: do not guess duplicates.
                identity = md5 or f"__cache__{record.cache_id}"
                by_md5.setdefault(identity, []).append(record)
            groups.extend(by_md5.values())
        return groups

    def representative(self, records: Iterable[MineruRecord]) -> MineruRecord:
        """Choose the cache whose parent Zotero metadata is most complete."""
        candidates = list(records)
        if not candidates:
            raise MineruNotFoundError("Cannot choose from an empty cache group")
        return max(candidates, key=self._record_quality_key)

    def candidates_for_key(self, key: str) -> list[MineruRecord]:
        ref = key.strip().upper()
        if ref.isdigit():
            return [r for r in self.records() if r.cache_id == int(ref)]

        attachment_matches = [
            r for r in self.records() if r.attachment_key == ref
        ]
        if attachment_matches:
            return self._dedupe_attachment_versions(attachment_matches)

        return [r for r in self.records() if r.parent_item_key == ref]

    def candidates_for_identifiers(
        self, identifiers: "PaperIdentifiers"
    ) -> list[MineruRecord]:
        """Resolve an arXiv ID / DOI / URL reference via the library index.

        This is the bridge the catalog previously lacked: agents hold an arXiv
        ID or DOI, but caches are keyed on Zotero attachment/parent keys. The
        library index maps identifiers to parent keys using item metadata.
        """
        if identifiers.is_empty:
            return []

        index = self.library_index()
        if index is None:
            return []

        entry = index.resolve(
            arxiv_id=identifiers.arxiv_id,
            doi=identifiers.doi,
            item_key=identifiers.zotero_key,
        )
        if entry is None:
            return []

        matched = [
            record
            for record in self.records()
            if (record.parent_item_key or "").upper() == entry.item_key.upper()
        ]
        if matched:
            return matched

        # Fall back to the specific PDF attachment when the parent has several.
        for attachment_key in entry.pdf_attachment_keys:
            matched.extend(
                record
                for record in self.records()
                if (record.attachment_key or "").upper() == attachment_key.upper()
            )
        return matched

    def library_index(self) -> "LibraryIndex | None":
        """Lazily build the identifier → item index, tolerating API failure."""
        if self._library_index is False:
            return None
        if self._library_index is None:
            try:
                from .library_index import build_library_index

                self._library_index = build_library_index(self._config)
            except Exception:
                self._library_index = False
        return self._library_index or None

    def resolve(
        self,
        reference: str,
        *,
        match: str | None = None,
        use_identifiers: bool = True,
    ) -> MineruRecord:
        ref = reference.strip()
        candidates: list[MineruRecord] = []

        # 1. Exact Zotero key / cache id / (rarely) a filename that is an ID.
        candidates = self.candidates_for_key(ref)

        # 2. arXiv ID / DOI / URL resolution through the library index.
        if not candidates and use_identifiers:
            identifiers = extract_identifiers(ref)
            candidates = self.candidates_for_identifiers(identifiers)

        # 3. Last resort: normalized title/substring matching.
        if not candidates:
            needle = _normalize_text(ref)
            candidates = [r for r in self.records() if self._matches_text(r, needle)]

        if match:
            match_needle = _normalize_text(match)
            candidates = [r for r in candidates if self._matches_text(r, match_needle)]

        if not candidates:
            raise MineruNotFoundError(f"No MinerU cache found for {reference!r}")
        if len(candidates) == 1:
            return candidates[0]

        # Collapse duplicate caches of the *same* paper before declaring
        # ambiguity. Two caches that share a parent item are the same paper by
        # construction, so this is unambiguous and safe.
        collapsed = self._collapse_same_paper(candidates)
        if collapsed is not None:
            return collapsed

        default = self._resolve_parent_default(ref, candidates)
        if default is not None and not match:
            return default

        raise MineruAmbiguousError(reference, candidates)

    def _collapse_same_paper(
        self, candidates: list[MineruRecord]
    ) -> MineruRecord | None:
        """Return the best record when every candidate is the same paper.

        A shared parent item alone is *not* enough: one book can have several
        chapter PDFs, which are genuinely different documents and must stay
        ambiguous. Two caches are the same paper only when they share a parent
        **and** their attachment MD5s agree, or when they are literally the
        same attachment. Without MD5s we refuse to guess and stay ambiguous.
        """
        if any(not record.parent_item_key for record in candidates):
            return None
        parents = {(record.parent_item_key or "").upper() for record in candidates}
        if len(parents) != 1:
            return None

        attachment_keys = {
            (record.attachment_key or "").upper() for record in candidates
        }

        # Same attachment key → same PDF, no API call needed.
        if len(attachment_keys) == 1 and all(attachment_keys):
            return self.representative(candidates)

        if self.api is None:
            return None

        resolved_md5s = [self._attachment_md5(record) for record in candidates]
        # A missing hash means "unknown", not "equal" — never collapse on it.
        if any(md5 is None for md5 in resolved_md5s):
            return None
        if len(set(resolved_md5s)) == 1:
            return self.representative(candidates)
        return None

    def outline(self, record: MineruRecord) -> list[MineruSection]:
        text = self.read_full(record)
        raw_matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
        matches: list[tuple[re.Match[str], int, str | None]] = []
        seen_numbers: set[str] = set()
        for match in raw_matches:
            heading = _clean_heading(match.group(2))
            number = _section_number(heading)
            # MinerU occasionally emits the same numbered heading twice around
            # a figure. Treat the later occurrence as section content, not a
            # new logical section boundary.
            if number and number in seen_numbers:
                continue
            if number:
                seen_numbers.add(number)
            markdown_level = len(match.group(1))
            semantic_level = max(markdown_level, number.count(".") + 1) if number else markdown_level
            matches.append((match, semantic_level, number))

        sections: list[MineruSection] = []
        for index, (current, level, _) in enumerate(matches):
            self_end = matches[index + 1][0].start() if index + 1 < len(matches) else len(text)
            end = len(text)
            for following, following_level, _ in matches[index + 1 :]:
                if following_level <= level:
                    end = following.start()
                    break
            sections.append(
                MineruSection(
                    index=index,
                    level=level,
                    heading=_clean_heading(current.group(2)),
                    char_start=current.start(),
                    self_char_end=self_end,
                    char_end=end,
                )
            )
        return sections

    def read(
        self,
        record: MineruRecord,
        *,
        section: str | int | None = None,
        scope: str = "self",
        max_chars: int | None = None,
    ) -> MineruReadResult:
        text = self.read_full(record)
        selected: MineruSection | None = None
        if section is not None:
            selected = self.select_section(self.outline(record), section)
            end = selected.char_end if scope == "subtree" else selected.self_char_end
            text = text[selected.char_start : end].strip()

        original_chars = len(text)
        truncated = bool(max_chars and original_chars > max_chars)
        if truncated:
            text = text[: max_chars].rstrip() + "\n\n[TRUNCATED]"

        return MineruReadResult(
            record=record,
            content=text,
            section=selected,
            truncated=truncated,
            original_chars=original_chars,
        )

    def read_full(self, record: MineruRecord) -> str:
        try:
            return Path(record.full_md_path).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            raise MineruNotFoundError(
                f"Cannot read MinerU Markdown: {record.full_md_path}"
            ) from exc

    @staticmethod
    def select_section(
        sections: list[MineruSection], selector: str | int
    ) -> MineruSection:
        if isinstance(selector, int) or str(selector).strip().isdigit():
            index = int(selector)
            if index < 0 or index >= len(sections):
                raise MineruNotFoundError(
                    f"Section index {index} out of range (total {len(sections)})"
                )
            return sections[index]

        needle = _normalize_text(str(selector))
        raw_selector = str(selector).strip().rstrip(".")
        if re.fullmatch(r"\d+(?:\.\d+)*", raw_selector):
            numbered = [
                s for s in sections if _section_number(s.heading) == raw_selector
            ]
            if len(numbered) == 1:
                return numbered[0]

        exact = [s for s in sections if _normalize_text(s.heading) == needle]
        if len(exact) == 1:
            return exact[0]

        prefix = [
            s for s in sections if _normalize_text(s.heading).startswith(needle)
        ]
        if len(prefix) == 1:
            return prefix[0]

        partial = [s for s in sections if needle in _normalize_text(s.heading)]
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise MineruNotFoundError(f"No section matches {selector!r}")
        labels = ", ".join(f"{s.index}:{s.heading}" for s in partial[:8])
        raise MineruCatalogError(
            f"Section selector {selector!r} is ambiguous: {labels}"
        )

    def _scan(self) -> list[MineruRecord]:
        root = Path(self.cache_dir)
        if not root.is_dir():
            return []

        records: list[MineruRecord] = []
        for child in sorted(root.iterdir(), key=_numeric_path_sort_key):
            if not child.is_dir() or not child.name.isdigit():
                continue
            full_md = child / FULL_MD_FILENAME
            if not full_md.is_file():
                continue

            source = _read_json(child / SOURCE_FILENAME)
            manifest = child / MANIFEST_FILENAME
            title = _extract_title(full_md, source.get("sourceFilename"))
            records.append(
                MineruRecord(
                    cache_id=int(child.name),
                    cache_dir=str(child),
                    full_md_path=str(full_md),
                    manifest_path=str(manifest) if manifest.is_file() else None,
                    attachment_key=_upper_or_none(source.get("attachmentKey")),
                    parent_item_key=_upper_or_none(source.get("parentItemKey")),
                    source_filename=_string_or_none(source.get("sourceFilename")),
                    title=title,
                    origin=_string_or_none(source.get("origin")),
                    parsed_at=_string_or_none(source.get("parsedAt")),
                    source_version=_int_or_none(source.get("version")),
                )
            )
        return records

    def _matches_text(self, record: MineruRecord, needle: str) -> bool:
        if not needle:
            return True
        haystacks = (
            record.title,
            record.source_filename or "",
            record.attachment_key or "",
            record.parent_item_key or "",
            str(record.cache_id),
        )
        return any(needle in _normalize_text(value) for value in haystacks)

    def _resolve_parent_default(
        self, reference: str, candidates: list[MineruRecord]
    ) -> MineruRecord | None:
        if self.api is None or len(reference.strip()) != 8:
            return None
        try:
            item = self.api.get_item(reference.strip().upper())
        except Exception:
            return None
        href = item.get("links", {}).get("attachment", {}).get("href", "")
        default_key = href.rstrip("/").split("/")[-1].upper()
        matches = [r for r in candidates if r.attachment_key == default_key]
        return matches[0] if len(matches) == 1 else None

    def _get_api_item(self, key: str | None) -> dict | None:
        if not key or self.api is None:
            return None
        if key not in self._api_items:
            try:
                item = self.api.get_item(key)
                self._api_items[key] = item if isinstance(item, dict) else None
            except Exception:
                self._api_items[key] = None
        return self._api_items[key]

    def _attachment_md5(self, record: MineruRecord) -> str | None:
        """PDF MD5, from the Local API or (preferably) SQLite.

        SQLite first: ``itemAttachments.storageHash`` is byte-identical to the
        API's ``md5`` field (verified), and it works with Zotero closed — which
        matters because duplicate-cache collapsing depends on it.
        """
        offline = self._offline_storage_hash(record.attachment_key)
        if offline:
            return offline
        item = self._get_api_item(record.attachment_key)
        value = item.get("data", {}).get("md5") if item else None
        return value.lower() if isinstance(value, str) and value else None

    def _offline_storage_hash(self, attachment_key: str | None) -> str | None:
        """Read ``storageHash`` from a read-only Zotero DB copy, if available."""
        if not attachment_key or self._config is None:
            return None
        if self._storage_hashes is None:
            self._storage_hashes = {}
            try:
                from .zotero_db import ZoteroDatabase

                with ZoteroDatabase(self._config.data_dir) as database:
                    for key, md5 in database.connect().execute(
                        """
                        SELECT i.key, a.storageHash
                        FROM itemAttachments a
                        JOIN items i ON i.itemID = a.itemID
                        WHERE a.storageHash IS NOT NULL AND a.storageHash <> ''
                        """
                    ):
                        self._storage_hashes[str(key).upper()] = str(md5).lower()
            except Exception:
                # Offline DB unavailable: fall back to the API path.
                pass
        return self._storage_hashes.get(attachment_key.upper())

    def _record_quality_key(self, record: MineruRecord) -> tuple[int, float, int]:
        score = 0
        parent = self._get_api_item(record.parent_item_key)
        data = parent.get("data", {}) if parent else {}
        title = data.get("title")
        if isinstance(title, str) and len(title.strip()) >= 12:
            score += 2
        creators = data.get("creators")
        if isinstance(creators, list) and creators:
            score += 3
        for field in ("date", "DOI", "ISBN", "url", "publisher"):
            if data.get(field):
                score += 1
        tags = data.get("tags")
        if isinstance(tags, list) and tags:
            score += 1
        freshness, cache_id = _record_freshness_key(record)
        return (score, freshness, cache_id)

    @staticmethod
    def _dedupe_attachment_versions(
        records: list[MineruRecord],
    ) -> list[MineruRecord]:
        if len(records) <= 1:
            return records
        return [max(records, key=_record_freshness_key)]


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _extract_title(full_md: Path, source_filename: object) -> str:
    try:
        with full_md.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(80):
                line = handle.readline()
                if not line:
                    break
                match = re.match(r"^#{1,6}\s+(.+?)\s*$", line.strip())
                if match:
                    heading = _clean_heading(match.group(1))
                    if heading and not heading.startswith("![]("):
                        return heading
    except OSError:
        pass

    filename = _string_or_none(source_filename)
    if filename:
        return _title_from_filename(filename)
    return full_md.parent.name


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"^\s*\d+\s*[-_]\s*", "", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _clean_heading(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+#+\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _section_number(heading: str) -> str | None:
    match = re.match(r"^(\d+(?:\.\d+)+)\.?(?:\s|$)", heading)
    return match.group(1) if match else None


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.lower()).strip()


def _string_or_none(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _upper_or_none(value: object) -> str | None:
    text = _string_or_none(value)
    return text.upper() if text else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _numeric_path_sort_key(path: Path) -> tuple[int, str]:
    return (int(path.name), path.name) if path.name.isdigit() else (10**18, path.name)


def _record_freshness_key(record: MineruRecord) -> tuple[float, int]:
    timestamp = 0.0
    if record.parsed_at:
        try:
            timestamp = datetime.fromisoformat(
                record.parsed_at.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            timestamp = 0.0
    try:
        timestamp = max(timestamp, os.path.getmtime(record.full_md_path))
    except OSError:
        pass
    return (timestamp, record.cache_id)
