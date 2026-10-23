"""Source-only deep notes. Personal vocabulary is loaded from private config.

Legacy private sections truncate to EOF, exactly as in the publisher.
New contract 2 notes keep personal analysis in separate private documents.
"""
from __future__ import annotations

import re
from workflow_config import private_terms

PROJECT_TERMS = ("当前项目", "我的项目", "本地项目", "我们项目", "我们的项目", "我的判读")
PIPELINE_TERMS: tuple[str, ...] = ()
PUBLIC_REGION_FORBIDDEN = PROJECT_TERMS  # Private terms are loaded at validation time.
SECTION_REF_RE = re.compile(r"§\s*(\d+)")
HEADING_NUMBER_RE = re.compile(r"^#{1,6}\s+(?:(\d+)[.、]|§\s*(\d+))", re.MULTILINE)
PRIVATE_PARTITIONS = ("01-Projects", "00-Inbox", "03-Concepts", "04-Experiments",
                      "05-Output", "06-Lecture", "07-Templates", "Life", "private")
PERSONAL_SECTION_RE = re.compile(
    r"^#{1,6}\s+(?:\d+[.、]\s*)?(?:项目关联(?:与本笔记判读)?|本笔记判读|我的(?:判读|判断|批注|理解|启发|看法|评价)|私人笔记|Personal Notes|My Notes)(?:\s|[:：（(]|$)",
    re.MULTILINE | re.IGNORECASE,
)
# Explicit divider: everything from here to EOF is private by construction and
# is dropped before publication, whatever its heading. Unlike a personal-heading
# match this needs no keyword, so a review/caveat section titled like ordinary
# paper content is still hidden.
PRIVATE_MARKER_RE = re.compile(
    r"^\s*(?:<!--\s*private\b[^>]*-->\s*|<<<\s*private\s*>>>)\s*$", re.IGNORECASE
)
RELATED_SECTION_RE = re.compile(r"^#{2,3}\s+关联\s*$", re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def _visible_lines(content: str):
    """Ignore fenced code when locating headings; preserve source offsets."""
    fence = None
    offset = 0
    for line in content.splitlines(keepends=True):
        m = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
        elif fence is None:
            yield offset, line
        offset += len(line)


def public_region(content: str) -> str:
    for offset, line in _visible_lines(content):
        if PRIVATE_MARKER_RE.match(line):
            return content[:offset]
        if PERSONAL_SECTION_RE.match(re.sub(r"[*_`]", "", line)):
            return content[:offset]
    return content


def _divider_region(content: str) -> str:
    """Content above the private divider only (legacy personal headings do not
    truncate). Used where the *presence* of a legacy personal heading is itself
    the finding."""
    for offset, line in _visible_lines(content):
        if PRIVATE_MARKER_RE.match(line):
            return content[:offset]
    return content


def has_personal_section(content: str) -> bool:
    # Everything below the divider is sanctioned (never published). A personal
    # heading above it — the legacy, un-migrated case — is an error.
    for _, line in _visible_lines(_divider_region(content)):
        if PERSONAL_SECTION_RE.match(re.sub(r"[*_`]", "", line)):
            return True
    return False


def has_related_section(content: str) -> bool:
    return any(RELATED_SECTION_RE.match(line) for _, line in _visible_lines(content))


def find_dangling_references(content: str) -> list[tuple[int, str]]:
    """Public `§N` cross-references whose target heading is only below the divider.

    The private divider drops everything after it before publication, so a
    public-region pointer such as "见 §10" dangles on the published page when
    section 10 lives below the line. Only numbers that name a heading in the
    hidden tail are reported, so references to the *paper's* own sections (which
    usually reuse the same numbering) are not falsely flagged.
    """
    public = public_region(content)
    hidden = {
        n for m in HEADING_NUMBER_RE.finditer(content)
        for n in (m.group(1) or m.group(2),) if n
    } - {
        n for m in HEADING_NUMBER_RE.finditer(public)
        for n in (m.group(1) or m.group(2),) if n
    }
    return [
        (n, num)
        for n, line in enumerate(public.splitlines(), 1)
        for num in SECTION_REF_RE.findall(line)
        if num in hidden
    ]


def find_public_region_violations(content: str) -> list[tuple[int, str, str]]:
    # Inline comments cannot bypass checks. Never include private terms or
    # original excerpts in results that may reach public CI logs.
    forbidden = tuple(dict.fromkeys(PROJECT_TERMS + private_terms()))
    return [(n, next(t for t in forbidden if t.casefold() in line.casefold()), "")
            for n, line in enumerate(public_region(content).splitlines(), 1)
            if any(t.casefold() in line.casefold() for t in forbidden)]


def find_private_wikilinks(content: str) -> list[tuple[int, str]]:
    return [(n, target.strip())
            for n, line in enumerate(public_region(content).splitlines(), 1)
            for target in WIKILINK_RE.findall(line)
            if any(target.strip().lstrip("/").casefold().startswith(p.casefold() + "/")
                   for p in PRIVATE_PARTITIONS)]


def validate_note(content: str) -> list[str]:
    """Privacy for all writes; evidence structure for contract 2 notes."""
    import yaml
    issues = []
    m = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.S)
    if not m:
        return ["frontmatter-missing"]
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return ["frontmatter-invalid"]
    if not isinstance(fm, dict):
        return ["frontmatter-invalid"]
    if find_public_region_violations(content):
        issues.append("private-context")
    if find_private_wikilinks(content):
        issues.append("private-link")
    if fm.get("note_contract") == 2:
        body = content[m.end():].lstrip()
        if has_personal_section(content):
            issues.append("personal-section-in-source-note")
        if find_dangling_references(content):
            issues.append("dangling-reference")
        if not body.startswith("## 原文与代码"):
            issues.append("sources-must-be-first")
        if len(re.findall(r"(?:§\s*\d|[图表]\s*\d|(?:Fig(?:ure)?|Table|Eq(?:uation)?)\.?\s*\d)", body, re.I)) < 2:
            issues.append("source-locators-missing")
        if len(re.findall(r"^##\s+", body, re.M)) < 4:
            issues.append("deep-analysis-sections-missing")
    return issues


def require_valid_note(content: str) -> None:
    issues = validate_note(content)
    if issues:
        raise ValueError("Note validation failed: " + ", ".join(issues))
