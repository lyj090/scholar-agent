#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone MinerU API client — parse a PDF without the Zotero plugin.

Reverse-engineered from llm-for-zotero v3.9.7 (``src/utils/mineruClient.ts``).
Two modes, matching the plugin exactly:

``cloud``
    ``POST https://mineru.net/api/v4/file-urls/batch`` → presigned PUT → poll
    ``GET /extract-results/batch/{batch_id}`` → download ``full_zip_url``.

``local``
    ``POST {base}/file_parse`` (multipart) — the response body *is* the ZIP.

Deliberate differences from the plugin, and why:

* **No third-party HTTP stack.** The plugin falls back to ``curl`` because it
  runs inside Zotero/Firefox with a broken CORS situation. Here ``urllib`` and
  ``requests`` (when available) are enough.
* **No built-in/proxy API.** The keyless community proxy existed in v3.8.14 and
  was removed in v3.9.7; the README still mentions it, but the code and its
  regression tests do not. We require a real API key.
* **Poll interval/timeout constants are preserved** so behaviour matches the
  plugin an operator already trusts.

NOT responsible for: cache writing (see ``mineru_cache_writer``), Zotero IDs,
CLI output.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field

__all__ = [
    "MineruParseError",
    "MineruRateLimitError",
    "MineruAuthError",
    "MineruConfigError",
    "MineruResult",
    "MineruClient",
    "read_mineru_api_key",
    "DEFAULT_CLOUD_BASE",
    "DEFAULT_LOCAL_BASE",
]


DEFAULT_CLOUD_BASE = "https://mineru.net/api/v4"
DEFAULT_LOCAL_BASE = "http://127.0.0.1:8000"

# Preserved from mineruClient.ts:34-49 so external behaviour matches the plugin.
CLOUD_INITIAL_POLL_INTERVAL_MS = 3000
CLOUD_MEDIUM_POLL_INTERVAL_MS = 15 * 1000
CLOUD_LONG_ACTIVE_POLL_INTERVAL_MS = 60 * 1000
CLOUD_MEDIUM_POLL_AFTER_MS = 5 * 60 * 1000
CLOUD_LONG_ACTIVE_POLL_AFTER_MS = 30 * 60 * 1000
CLOUD_NO_STATUS_TIMEOUT_MS = 10 * 60 * 1000
CLOUD_PRE_PROCESSING_TIMEOUT_MS = 30 * 60 * 1000
REQUEST_TIMEOUT_MS = 60 * 1000
LOCAL_BUSY_RETRY_DELAYS_MS = (5000, 15000, 30000, 60000, 120000)

_ACTIVE_STATES = frozenset({"running", "converting"})
_RATE_LIMIT_RE = re.compile(r"rate.?limit|quota|exceeded|limit.*reached", re.I)

# The plugin hashes the upload filename to keep deep MinerU output paths short.
UPLOAD_FILENAME_HASH_HEX = 12


class MineruParseError(Exception):
    """Base class for MinerU parse failures."""


class MineruConfigError(MineruParseError):
    """Missing or invalid configuration (no API key, bad local base)."""


class MineruRateLimitError(MineruParseError):
    """Daily quota / rate limit reached — resuming later is the only fix."""


class MineruAuthError(MineruParseError):
    """API key rejected (HTTP 401/403)."""


@dataclass
class MineruResult:
    """A parsed paper: normalized markdown plus every extracted file."""

    markdown: str
    files: dict[str, bytes] = field(default_factory=dict)
    content_list: list | None = None
    manifest: dict | None = None

    @property
    def images(self) -> dict[str, bytes]:
        return {
            name: data
            for name, data in self.files.items()
            if name.startswith("images/")
        }


def read_mineru_api_key(prefs_path: str | None = None) -> str | None:
    """Read the MinerU API key from Zotero's ``prefs.js``.

    The plugin stores it as a global Zotero pref
    (``extensions.zotero.llmforzotero.mineruApiKey``), which Firefox serialises
    into the profile's ``prefs.js``. This is the same technique the repo already
    uses for the MCP bearer token, so the key never needs to be duplicated into
    another config file.
    """
    import glob

    if prefs_path:
        candidates = [prefs_path]
    else:
        pattern = os.path.expanduser("~/.zotero/zotero/*/prefs.js")
        candidates = sorted(glob.glob(pattern))

    pattern_re = re.compile(
        r'user_pref\(\s*"extensions\.zotero\.llmforzotero\.mineruApiKey"\s*,\s*"([^"]*)"\s*\)'
    )
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                match = pattern_re.search(handle.read())
        except OSError:
            continue
        if match and match.group(1).strip():
            return match.group(1).strip()
    return None


def _ascii_upload_name(path: str) -> str:
    raw = os.path.basename(path) or "paper.pdf"
    cleaned = re.sub(r"[^\x20-\x7E]", "_", raw)
    return cleaned or "paper.pdf"


def _hashed_upload_name(path: str) -> str:
    import hashlib

    raw = os.path.basename(path) or "paper.pdf"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{digest[:UPLOAD_FILENAME_HASH_HEX]}.pdf"


def _http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = REQUEST_TIMEOUT_MS / 1000.0,
    suppress_default_content_type: bool = False,
) -> tuple[int, bytes, dict[str, str]]:
    """Perform one HTTP request, returning ``(status, body, headers)``.

    Non-2xx responses are returned rather than raised so callers can map them to
    domain errors (401 vs 429 mean very different things here).

    ``suppress_default_content_type`` exists for presigned uploads: ``urllib``
    adds ``Content-Type: application/x-www-form-urlencoded`` to any request with
    a body, and that extra header is *not* part of the signed URL, so Alibaba
    OSS rejects the upload with HTTP 403. Setting an empty Content-Type header
    makes urllib omit it entirely (verified: without it the same PUT returns
    200, with it 403) — the same trap the plugin documents at
    ``mineruClient.ts:1550-1551``.
    """
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if suppress_default_content_type:
        request.add_header("Content-Type", "")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except OSError:
            body = b""
        return exc.code, body, dict(exc.headers or {})


class MineruClient:
    """Parse PDFs via the MinerU cloud or a local self-hosted server."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        mode: str = "cloud",
        cloud_base: str = DEFAULT_CLOUD_BASE,
        local_base: str = DEFAULT_LOCAL_BASE,
        cloud_model: str = "vlm",
        local_backend: str = "pipeline",
        force_ocr: bool = False,
        poll_interval_s: float | None = None,
        timeout_s: float | None = None,
        verbose: bool = False,
    ):
        self.api_key = (api_key or "").strip()
        self.mode = "local" if str(mode).strip().lower() == "local" else "cloud"
        self.cloud_base = cloud_base.rstrip("/")
        self.local_base = local_base.rstrip("/")
        self.cloud_model = cloud_model
        self.local_backend = local_backend
        self.force_ocr = bool(force_ocr)
        # Test hooks: shrink waiting without changing production defaults.
        self._poll_scale = 1.0
        if poll_interval_s is not None:
            self._poll_override_s = float(poll_interval_s)
        else:
            self._poll_override_s = None
        self.timeout_s = timeout_s
        self.verbose = verbose

    # -- logging ---------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[mineru] {message}", flush=True)

    # -- public API ------------------------------------------------------

    def parse_pdf(self, pdf_path: str) -> MineruResult:
        """Parse one PDF and return normalized markdown + files."""
        if not os.path.isfile(pdf_path):
            raise MineruConfigError(f"PDF not found: {pdf_path}")
        if self.mode == "local":
            return self._parse_local(pdf_path)
        return self._parse_cloud(pdf_path)

    def test_connection(self) -> tuple[bool, str]:
        """Cheaply verify credentials/reachability. Returns ``(ok, message)``."""
        if self.mode == "local":
            status, _body, _headers = _http_request(
                "GET", f"{self.local_base}/health", timeout=10
            )
            if 200 <= status < 300:
                return True, f"local MinerU healthy at {self.local_base}"
            return False, f"local MinerU health check failed: HTTP {status}"

        if not self.api_key:
            return False, "no MinerU API key (set --api-key or Zotero prefs)"
        status, body, _headers = _http_request(
            "GET",
            f"{self.cloud_base}/extract-results/batch/_test",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=20,
        )
        if status in (401, 403):
            raise MineruAuthError("Invalid MinerU API key — authentication failed")
        if 200 <= status < 300:
            return True, "cloud MinerU credentials accepted"
        # A 200-with-error-body is normal for the probe endpoint.
        return True, f"credentials reached MinerU (HTTP {status})"

    # -- cloud -----------------------------------------------------------

    def _parse_cloud(self, pdf_path: str) -> MineruResult:
        if not self.api_key:
            raise MineruConfigError(
                "MinerU API key required for cloud mode. Pass --api-key or set "
                "extensions.zotero.llmforzotero.mineruApiKey in Zotero prefs."
            )

        with open(pdf_path, "rb") as handle:
            pdf_bytes = handle.read()
        if not pdf_bytes:
            raise MineruParseError(f"PDF is empty: {pdf_path}")

        file_name = _ascii_upload_name(pdf_path)
        payload = {
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
            "model_version": self.cloud_model,
            "files": [{"name": file_name, "is_ocr": self.force_ocr}],
        }
        self._log(f"requesting upload URL ({len(pdf_bytes) / 1e6:.1f} MB)")
        status, body, _ = _http_request(
            "POST",
            f"{self.cloud_base}/file-urls/batch",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8"),
            timeout=60,
        )
        parsed = _json_or_none(body)

        if status == 429:
            raise MineruRateLimitError("MinerU daily quota exceeded (HTTP 429)")
        if status in (401, 403):
            raise MineruAuthError(f"MinerU rejected the API key (HTTP {status})")
        if not (200 <= status < 300):
            message = (parsed or {}).get("msg") if isinstance(parsed, dict) else ""
            if message and _RATE_LIMIT_RE.search(str(message)):
                raise MineruRateLimitError(f"MinerU rate limit: {message}")
            raise MineruParseError(
                f"batch request failed: HTTP {status} {str(message)[:200]}"
            )

        data = (parsed or {}).get("data") or {}
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls") or []
        if not batch_id or not file_urls:
            raise MineruParseError("MinerU returned no batch_id/file_urls")

        self._log("uploading PDF")
        # No Content-Type: the presigned OSS signature may not include it, and
        # sending one yields HTTP 403 (mineruClient.ts:1550-1551). urllib would
        # otherwise add a default, so suppress it explicitly.
        upload_status, _body, _headers = _http_request(
            "PUT",
            file_urls[0],
            data=pdf_bytes,
            timeout=180,
            suppress_default_content_type=True,
        )
        if not (200 <= upload_status < 300):
            raise MineruParseError(
                f"upload failed: HTTP {upload_status} to {_host(file_urls[0])}"
            )

        zip_bytes = self._poll_cloud(batch_id)
        return _unzip_result(zip_bytes)

    def _poll_cloud(self, batch_id: str) -> bytes:
        poll_start = time.monotonic()
        last_status: float | None = None
        active_start: float | None = None

        while True:
            interval_s = self._cloud_interval(poll_start, active_start)
            self._log(f"polling in {interval_s:.0f}s")
            time.sleep(interval_s)

            status, body, _ = _http_request(
                "GET",
                f"{self.cloud_base}/extract-results/batch/{batch_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=60,
            )
            now = time.monotonic()
            if not (200 <= status < 300):
                self._log(f"poll HTTP {status}; continuing")
                continue

            parsed = _json_or_none(body) or {}
            results = ((parsed.get("data") or {}).get("extract_result")) or []
            if not results:
                self._log("poll response has no extract_result")
                continue
            entry = results[0]
            state = str(entry.get("state") or "").strip().lower()
            if not state:
                continue

            last_status = now
            if state in _ACTIVE_STATES and active_start is None:
                active_start = now
            self._log(f"state={state}")

            if state == "done":
                zip_url = entry.get("full_zip_url")
                if not zip_url:
                    raise MineruParseError("MinerU reported done but sent no ZIP URL")
                self._log("downloading result ZIP")
                download_status, zip_bytes, _ = _http_request(
                    "GET", zip_url, timeout=300
                )
                if not (200 <= download_status < 300):
                    raise MineruParseError(
                        f"ZIP download failed: HTTP {download_status}"
                    )
                return zip_bytes
            if state == "failed":
                raise MineruParseError("MinerU extraction failed on the server")

            elapsed_since_status = now - (last_status or poll_start)
            if elapsed_since_status >= CLOUD_NO_STATUS_TIMEOUT_MS / 1000.0:
                raise MineruParseError("timed out waiting for MinerU status")
            if (
                active_start is None
                and now - poll_start >= CLOUD_PRE_PROCESSING_TIMEOUT_MS / 1000.0
            ):
                raise MineruParseError("timed out before MinerU started processing")

    def _cloud_interval(self, poll_start: float, active_start: float | None) -> float:
        if self._poll_override_s is not None:
            return self._poll_override_s
        now = time.monotonic()
        if (
            active_start is not None
            and (now - active_start) * 1000 >= CLOUD_LONG_ACTIVE_POLL_AFTER_MS
        ):
            return CLOUD_LONG_ACTIVE_POLL_INTERVAL_MS / 1000.0
        if (now - poll_start) * 1000 >= CLOUD_MEDIUM_POLL_AFTER_MS:
            return CLOUD_MEDIUM_POLL_INTERVAL_MS / 1000.0
        return CLOUD_INITIAL_POLL_INTERVAL_MS / 1000.0

    # -- local -----------------------------------------------------------

    def _parse_local(self, pdf_path: str) -> MineruResult:
        with open(pdf_path, "rb") as handle:
            pdf_bytes = handle.read()
        if not pdf_bytes:
            raise MineruParseError(f"PDF is empty: {pdf_path}")

        backend = {
            "pipeline": "pipeline",
            "vlm": "vlm-auto-engine",
            "hybrid": "hybrid-auto-engine",
        }.get(self.local_backend, "pipeline")

        fields = {
            "backend": backend,
            "parse_method": "ocr" if self.force_ocr else "auto",
            "formula_enable": "true",
            "table_enable": "true",
            "return_md": "true",
            "return_content_list": "true",
            "return_images": "true",
            "response_format_zip": "true",
            "return_original_file": "false",
        }

        for attempt, delay_ms in enumerate((*LOCAL_BUSY_RETRY_DELAYS_MS, None)):
            body, content_type = _multipart(
                files={"files": (_hashed_upload_name(pdf_path), pdf_bytes, "application/pdf")},
                fields=fields,
            )
            self._log(f"POST {self.local_base}/file_parse")
            status, response_body, _ = _http_request(
                "POST",
                f"{self.local_base}/file_parse",
                headers={"Content-Type": content_type},
                data=body,
                # The plugin sets no timeout here (long local parses are normal).
                timeout=self.timeout_s or 3600,
            )
            if status == 409:
                if delay_ms is None:
                    raise MineruParseError(
                        "local MinerU server still busy after all retries"
                    )
                self._log(f"server busy; retrying in {delay_ms / 1000:.0f}s")
                time.sleep(delay_ms / 1000.0)
                continue
            if not (200 <= status < 300):
                raise MineruParseError(
                    f"local parse failed: HTTP {status} {response_body[:200]!r}"
                )
            return _unzip_result(response_body)

        raise MineruParseError("local parse exhausted retries")


# -- helpers -------------------------------------------------------------


def _host(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url)
    return match.group(1) if match else url[:60]


def _json_or_none(body: bytes):
    try:
        return json.loads(body.decode("utf-8") or "null")
    except (ValueError, UnicodeDecodeError):
        return None


def _multipart(
    *, files: dict[str, tuple[str, bytes, str]], fields: dict[str, str]
) -> tuple[bytes, str]:
    """Build a multipart/form-data body without external dependencies."""
    boundary = "----MineruBoundary" + os.urandom(12).hex()
    buffer = io.BytesIO()
    for name, value in fields.items():
        buffer.write(f"--{boundary}\r\n".encode())
        buffer.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        buffer.write(str(value).encode("utf-8"))
        buffer.write(b"\r\n")
    for name, (filename, data, content_type) in files.items():
        buffer.write(f"--{boundary}\r\n".encode())
        buffer.write(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        buffer.write(f"Content-Type: {content_type}\r\n\r\n".encode())
        buffer.write(data)
        buffer.write(b"\r\n")
    buffer.write(f"--{boundary}--\r\n".encode())
    return buffer.getvalue(), f"multipart/form-data; boundary={boundary}"


def _unzip_result(zip_bytes: bytes) -> MineruResult:
    """Validate and unpack a MinerU result ZIP.

    Mirrors ``mineruZip.ts``: check the ZIP signature before inflating, skip
    directory entries and ``__MACOSX/``, and require non-blank markdown.
    """
    if len(zip_bytes) < 4:
        raise MineruParseError("result is not a ZIP (too short)")
    if not (
        zip_bytes[0] == 0x50
        and zip_bytes[1] == 0x4B
        and zip_bytes[2] in (0x03, 0x05, 0x07)
    ):
        raise MineruParseError(
            f"result is not a ZIP (starts with {zip_bytes[:4].hex()})"
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise MineruParseError(f"ZIP extract failed: {exc}") from exc

    files: dict[str, bytes] = {}
    for info in archive.infolist():
        name = info.filename
        if name.endswith("/") or name.startswith("__MACOSX/"):
            continue
        files[name] = archive.read(info)

    markdown_name = _pick_markdown(files)
    if markdown_name is None:
        raise MineruParseError("no Markdown found in MinerU result")
    markdown = files[markdown_name].decode("utf-8", errors="replace")
    if not markdown.strip():
        raise MineruParseError("MinerU returned empty Markdown")

    content_list = None
    for name, data in files.items():
        if os.path.basename(name).endswith("content_list.json"):
            try:
                content_list = json.loads(data.decode("utf-8"))
            except ValueError:
                content_list = None
            break

    manifest = None
    for name, data in files.items():
        if os.path.basename(name) == "manifest.json":
            try:
                manifest = json.loads(data.decode("utf-8"))
            except ValueError:
                manifest = None
            break

    return MineruResult(
        markdown=markdown,
        files=files,
        content_list=content_list,
        manifest=manifest,
    )


def _pick_markdown(files: dict[str, bytes]) -> str | None:
    for name in files:
        if re.search(r"(^|[\\/])full\.md$", name, re.IGNORECASE):
            return name
    for name in files:
        if name.lower().endswith(".md"):
            return name
    return None
