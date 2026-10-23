#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metadata_fetch.py — turn an arXiv ID or DOI into Zotero item JSON, offline of Zotero.

Why this exists
---------------
Importing used to run ``Zotero.Translate.Search`` inside Zotero's process via the
plugin, which meant the import path could not work without the plugin installed.
Fetching the metadata ourselves and writing the item through
:mod:`zotero.local_write` is both simpler and inspectable: what gets written is
exactly what this module returns.

The mapping targets Zotero's own field vocabulary (``preprint`` for arXiv,
``journalArticle`` for DOI), matching what Zotero's translators produce for the
same identifiers where those fields exist.

NOT responsible for: writing to Zotero, PDF parsing, deduplication.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
_ATOM = "{http://www.w3.org/2005/Atom}"

#: Crossref/DataCite types -> Zotero item types.
_CROSSREF_TYPE_MAP = {
    "journal-article": "journalArticle",
    "proceedings-article": "conferencePaper",
    "book-chapter": "bookSection",
    "book": "book",
    "monograph": "book",
    "report": "report",
    "dissertation": "thesis",
    "posted-content": "preprint",
    "dataset": "dataset",
}


class MetadataError(RuntimeError):
    """Raised when metadata cannot be fetched for an identifier."""


def _http_get(url: str, *, timeout: float = 25.0, headers: dict | None = None) -> bytes:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", "scholar-agent/1.0 (local literature tool)")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise MetadataError(f"HTTP {error.code} for {url}") from error
    except (urllib.error.URLError, OSError) as error:
        raise MetadataError(f"Cannot reach {url}: {error}") from error


def _split_name(full: str) -> dict:
    """Split a display name into Zotero's creator shape.

    arXiv gives "Given Family"; anything without a clear split is written as a
    single-field name so Zotero does not invent a wrong first/last division.
    """
    name = " ".join((full or "").split())
    if not name:
        return {"creatorType": "author", "name": ""}
    parts = name.split(" ")
    if len(parts) >= 2:
        return {
            "creatorType": "author",
            "firstName": " ".join(parts[:-1]),
            "lastName": parts[-1],
        }
    return {"creatorType": "author", "name": name}


# ----------------------------------------------------------------------
#  arXiv
# ----------------------------------------------------------------------


def fetch_arxiv(arxiv_id: str) -> dict:
    """Fetch one arXiv paper as a Zotero ``preprint`` item."""
    clean = arxiv_id.strip()
    if not clean:
        raise MetadataError("Empty arXiv ID")
    url = f"{ARXIV_API}?{urllib.parse.urlencode({'id_list': clean})}"
    raw = _http_get(url)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise MetadataError(f"arXiv returned unparseable XML for {clean}") from error

    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        raise MetadataError(f"arXiv has no entry for {clean}")

    # arXiv answers a missing ID with a placeholder entry rather than an error.
    title_el = entry.find(f"{_ATOM}title")
    title = " ".join((title_el.text or "").split()) if title_el is not None else ""
    if not title or title.lower().startswith("error"):
        raise MetadataError(f"arXiv has no paper for {clean}")

    authors = []
    for author in entry.findall(f"{_ATOM}author"):
        name_el = author.find(f"{_ATOM}name")
        if name_el is not None and name_el.text:
            authors.append(_split_name(name_el.text))

    def _text(tag: str) -> str:
        el = entry.find(f"{_ATOM}{tag}")
        return (el.text or "").strip() if el is not None else ""

    published = _text("published")
    doi = ""
    journal_ref = ""
    primary = ""
    for link in entry.findall("{http://arxiv.org/schemas/atom}doi"):
        doi = (link.text or "").strip()
    jr = entry.find("{http://arxiv.org/schemas/atom}journal_ref")
    if jr is not None and jr.text:
        journal_ref = jr.text.strip()
    cat = entry.find("{http://arxiv.org/schemas/atom}primary_category")
    if cat is not None:
        primary = cat.get("term", "")

    # Strip any version suffix: Zotero's arXiv translator stores the bare ID in
    # archiveID and keeps the versioned form only in `extra`.
    bare = clean.split("v")[0] if "v" in clean else clean

    item: dict = {
        "itemType": "preprint",
        "title": title,
        "creators": authors,
        "abstractNote": " ".join(_text("summary").split()),
        "url": f"http://arxiv.org/abs/{bare}",
        "repository": "arXiv",
        "archiveID": f"arXiv:{bare}",
        "libraryCatalog": "arXiv.org",
        "date": published[:10],
        "tags": [],
    }
    extra_bits = []
    if primary:
        extra_bits.append(f"arXiv:{bare} [{primary}]")
    if journal_ref:
        extra_bits.append(f"Journal ref: {journal_ref}")
    if doi:
        extra_bits.append(f"DOI: {doi}")
    if extra_bits:
        item["extra"] = "\n".join(extra_bits)
    return item


# ----------------------------------------------------------------------
#  DOI
# ----------------------------------------------------------------------


def fetch_doi(doi: str) -> dict:
    """Fetch one DOI's metadata from Crossref as a Zotero item."""
    clean = doi.strip()
    if not clean:
        raise MetadataError("Empty DOI")
    url = f"{CROSSREF_API}/{urllib.parse.quote(clean, safe='')}"
    raw = _http_get(url)
    try:
        message = json.loads(raw.decode("utf-8"))["message"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
        raise MetadataError(f"Crossref returned unusable data for {clean}") from error

    titles = message.get("title") or []
    title = " ".join((titles[0] if titles else "").split())
    if not title:
        raise MetadataError(f"Crossref has no title for {clean}")

    creators = []
    for author in message.get("author") or []:
        if author.get("family"):
            creators.append({
                "creatorType": "author",
                "firstName": author.get("given", "") or "",
                "lastName": author["family"],
            })
        elif author.get("name"):
            creators.append({"creatorType": "author", "name": author["name"]})

    containers = message.get("container-title") or []
    container = containers[0] if containers else ""

    def _parts(field: str) -> str:
        block = message.get(field) or {}
        parts = (block.get("date-parts") or [[None]])[0]
        if not parts or not parts[0]:
            return ""
        year = str(parts[0])
        if len(parts) > 1 and parts[1]:
            return f"{year}-{int(parts[1]):02d}"
        return year

    date = _parts("published") or _parts("issued") or _parts("created")
    item_type = _CROSSREF_TYPE_MAP.get(message.get("type", ""), "journalArticle")

    item = {
        "itemType": item_type,
        "title": title,
        "creators": creators,
        "DOI": clean,
        "url": message.get("URL") or f"https://doi.org/{clean}",
        "date": date,
        "tags": [],
    }
    abstract = message.get("abstract")
    if abstract:
        # Crossref returns JATS XML; keep it readable rather than raw markup.
        import re as _re
        item["abstractNote"] = _re.sub(r"<[^>]+>", "", abstract).strip()
    if message.get("page"):
        item["pages"] = message["page"]
    if message.get("volume"):
        item["volume"] = str(message["volume"])
    if message.get("issue"):
        item["issue"] = str(message["issue"])
    if message.get("publisher"):
        item["publisher"] = message["publisher"]

    if item_type == "journalArticle":
        if container:
            item["publicationTitle"] = container
        issn = message.get("ISSN") or []
        if issn:
            item["ISSN"] = issn[0]
    elif item_type == "conferencePaper":
        if container:
            item["proceedingsTitle"] = container
    elif item_type == "bookSection":
        if container:
            item["bookTitle"] = container

    return item


def fetch(identifier: str, kind: str) -> dict:
    """Fetch by explicit ``kind`` ('arxiv' or 'doi')."""
    if kind == "arxiv":
        return fetch_arxiv(identifier)
    if kind == "doi":
        return fetch_doi(identifier)
    raise MetadataError(f"Unknown identifier kind: {kind!r}")
