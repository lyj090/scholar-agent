#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Academic identifier normalization — arXiv IDs, DOIs, and paper URLs.

The MinerU cache and Zotero native API are keyed on Zotero item keys, but
agents almost always hold an *academic identifier* (arXiv ID, DOI) or a URL.
This module is the single place that turns those opaque strings into a
canonical form so the catalog can look the paper up in the local library.

Design notes:
    - Pure string logic; no network, no Zotero dependency, trivially testable.
    - ``normalize_arxiv_id`` is deliberately strict: a bare number like
      ``1234.5678`` is a valid arXiv ID, but we do not want to match version
      suffixes or unrelated digits inside a longer token.
    - ``extract_identifiers`` returns *every* identifier a string contains so a
      caller can try them in order without re-parsing.

NOT responsible for: Zotero API calls, cache scanning, CLI output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "PaperIdentifiers",
    "normalize_arxiv_id",
    "normalize_doi",
    "extract_identifiers",
    "identifier_query_terms",
]


# Modern arXiv IDs: 2405.20321 (4-digit YYMM + 4-5 digit sequence).
# Legacy arXiv IDs: math/0211159, cs.CV/0701001, hep-th/9901001.
_NEW_ARXIV_RE = re.compile(r"(?<![0-9A-Za-z.])(\d{4}\.\d{4,5})(v\d+)?(?![0-9])")
_OLD_ARXIV_RE = re.compile(
    r"(?<![0-9A-Za-z])([a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7})(v\d+)?(?![0-9])",
    re.IGNORECASE,
)

# DOIs are "10.<registrant>/<suffix>"; stop the suffix at whitespace, quote,
# angle bracket, or a closing paren/bracket so trailing prose is not swallowed.
_DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"'<>\)\]]+)", re.IGNORECASE)

# arXiv's own DOI prefix. The embedded ID is what actually resolves locally.
_ARXIV_DOI_PREFIX = "10.48550/arxiv."

_STRIP_TRAILING = ".,;:)]}>"


def _clean_suffix(value: str) -> str:
    """Drop trailing punctuation that prose tends to glue onto identifiers."""
    return value.rstrip(_STRIP_TRAILING)


def normalize_arxiv_id(value: object) -> str | None:
    """Return a canonical bare arXiv ID (no version), or ``None``.

    Accepts ``2405.20321``, ``2405.20321v2``, ``arXiv:2405.20321``,
    ``arxiv.org/abs/2405.20321``, ``10.48550/arXiv.2405.20321`` and the legacy
    ``hep-th/9901001`` form. The returned value never carries a ``vN`` suffix
    because the local cache is version-agnostic.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    # Prefer a modern ID anywhere in the string (covers URLs and prefixed forms).
    match = _NEW_ARXIV_RE.search(text)
    if match:
        return match.group(1)

    # A bare DOI-for-arXiv carries the ID in its suffix.
    doi = _DOI_RE.search(text)
    if doi and doi.group(1).lower().startswith(_ARXIV_DOI_PREFIX):
        tail = doi.group(1)[len(_ARXIV_DOI_PREFIX) :]
        tail_match = _NEW_ARXIV_RE.search(tail)
        if tail_match:
            return tail_match.group(1)

    legacy = _OLD_ARXIV_RE.search(text)
    if legacy:
        return legacy.group(1).lower()
    return None


def normalize_doi(value: object) -> str | None:
    """Return a lowercase DOI without any resolver URL prefix, or ``None``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Strip common resolver prefixes before matching.
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    match = _DOI_RE.search(text)
    if not match:
        return None
    return _clean_suffix(match.group(1)).lower()


@dataclass(frozen=True)
class PaperIdentifiers:
    """Every identifier extractable from one user-supplied reference."""

    raw: str
    arxiv_id: str | None = None
    doi: str | None = None
    zotero_key: str | None = None
    title_hint: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.arxiv_id, self.doi, self.zotero_key, self.title_hint)
        )

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "arxiv_id": self.arxiv_id,
            "doi": self.doi,
            "zotero_key": self.zotero_key,
            "title_hint": self.title_hint,
        }


_ZOTERO_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")


def extract_identifiers(reference: object) -> PaperIdentifiers:
    """Parse one reference string into all identifiers it contains.

    The raw string is always preserved so callers can fall back to it for a
    title search. A string that is only an 8-character key is treated as a
    Zotero key rather than a title, and conversely a phrase that merely
    happens to be 8 characters long is kept as a title hint too.
    """
    if not isinstance(reference, str):
        reference = "" if reference is None else str(reference)
    raw = reference.strip()
    if not raw:
        return PaperIdentifiers(raw="")

    arxiv_id = normalize_arxiv_id(raw)
    doi = normalize_doi(raw)

    zotero_key = None
    candidate = raw.upper()
    if _ZOTERO_KEY_RE.fullmatch(candidate) and not arxiv_id and not doi:
        zotero_key = candidate

    # A title hint is useful only when the string is not purely an identifier.
    title_hint = None
    if not arxiv_id and not doi and not zotero_key:
        title_hint = raw
    elif raw and not (arxiv_id or doi):
        title_hint = raw

    return PaperIdentifiers(
        raw=raw,
        arxiv_id=arxiv_id,
        doi=doi,
        zotero_key=zotero_key,
        title_hint=title_hint,
    )


def identifier_query_terms(identifiers: PaperIdentifiers) -> list[str]:
    """Ordered search terms for a Zotero ``q=`` lookup.

    The native Local API cannot search by arXiv ID or DOI in these fields
    (verified: ``q=2506.09384`` returns zero hits), so these terms are a
    best-effort fallback. The authoritative path is the library index built
    from item metadata.
    """
    terms: list[str] = []
    if identifiers.arxiv_id:
        terms.append(identifiers.arxiv_id)
    if identifiers.doi:
        terms.append(identifiers.doi)
        if identifiers.doi.startswith(_ARXIV_DOI_PREFIX):
            terms.append(identifiers.doi[len(_ARXIV_DOI_PREFIX) :])
    if identifiers.title_hint:
        terms.append(identifiers.title_hint)
    return terms
