"""Deterministic public-source preamble; no private metadata enters it."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, unquote, parse_qsl
import ipaddress
import unicodedata
from workflow_config import section
import yaml


def public_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        value = unicodedata.normalize("NFKC", value.strip())
        for _ in range(3): value = unquote(value)
        u = urlsplit(value)
        host = (u.hostname or "").lower()
        if u.scheme not in ("https", "http") or not host or u.username or u.password:
            return None
        if "." not in host or host.endswith((".local", ".internal", ".localhost")):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        if any(host == h or host.endswith("." + h) for h in section("privacy").get("private_hosts", [])):
            return None
        if any(re.search(r"token|secret|key|auth|password|signature|credential|email", k, re.I) for k, _ in parse_qsl(u.query)):
            return None
        if any(c in value for c in "\n\r<>()"):
            return None
        return value.strip()
    except ValueError:
        return None


def ensure_source_links(content: str, data: dict) -> str:
    m = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.S)
    if not m:
        return content
    fm = yaml.safe_load(m.group(1))
    if not isinstance(fm, dict) or fm.get("note_contract") != 2:
        return content
    body = content[m.end():].lstrip()
    # Idempotently preserve an authored, verified opening. Validation/export
    # inspects its links separately; do not silently replace scientific content.
    if body.startswith("## 原文与代码"):
        return content
    doi = str(fm.get("doi") or data.get("DOI") or "").strip()
    original = public_url(fm.get("url")) or public_url(data.get("url"))
    if not original and doi.startswith("10."):
        original = public_url("https://doi.org/" + doi)
    if not original:
        raise ValueError("A verified original-source URL or DOI is required")
    links = ["## 原文与代码", f"- [原文]({original})"]
    for field, label in (("project_url", "论文主页"), ("code_url", "官方代码")):
        url = public_url(fm.get(field))
        if url:
            links.append(f"- [{label}]({url})")
    if not public_url(fm.get("code_url")):
        links.append("- 代码链接：未核实")
    return content[:m.end()].rstrip() + "\n\n" + "\n".join(links) + "\n\n" + body
