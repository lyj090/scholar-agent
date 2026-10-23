#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mineru_parse.py - Parse PDF/documents via MinerU Agent API

Uses MinerU's free Agent Lightweight API to convert PDFs and documents
to Markdown with full formula and table recognition.

Usage:
    python scripts/literature/mineru_parse.py <URL>
    python scripts/literature/mineru_parse.py <URL> -o output.md
    python scripts/literature/mineru_parse.py <URL> -d output_dir/
    python scripts/literature/mineru_parse.py <URL> --poll-interval 10

API: https://mineru.net/api/v1/agent (no auth required, IP rate-limited)
Limits: 10MB file size, 20 pages max, Markdown output only.

Dependencies:
    pip install requests
"""

import argparse
import os
import re
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    print("Error: This script requires 'requests'.")
    print("Please run: pip install requests")
    sys.exit(1)


# === Config ===

MINERU_BASE_URL = "https://mineru.net/api/v1/agent"
DEFAULT_POLL_INTERVAL = 5  # seconds
MAX_POLL_TIME = 300  # 5 minutes max wait


# === API Functions ===

def submit_url_task(url: str, language: str = "ch", enable_formula: bool = True,
                    enable_table: bool = True) -> Optional[str]:
    """Submit a URL for parsing. Returns task_id or None on failure."""
    endpoint = f"{MINERU_BASE_URL}/parse/url"
    payload = {
        "url": url,
        "language": language,
        "enable_formula": enable_formula,
        "enable_table": enable_table,
    }

    try:
        resp = requests.post(endpoint, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == 0:
            task_id = data["data"]["task_id"]
            return task_id
        else:
            print(f"[ERROR] API returned code={data.get('code')}: {data.get('msg')}", file=sys.stderr)
            return None
    except requests.RequestException as e:
        print(f"[ERROR] Failed to submit task: {e}", file=sys.stderr)
        return None


def poll_task_status(task_id: str, poll_interval: int = DEFAULT_POLL_INTERVAL,
                     max_time: int = MAX_POLL_TIME) -> Optional[dict]:
    """Poll task until done or timeout. Returns result dict or None."""
    endpoint = f"{MINERU_BASE_URL}/parse/{task_id}"
    elapsed = 0

    while elapsed < max_time:
        try:
            resp = requests.get(endpoint, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                print(f"[ERROR] Poll error: {data.get('msg')}", file=sys.stderr)
                return None

            state = data["data"].get("state", "")
            if state == "done":
                return data["data"]
            elif state in ("failed", "error"):
                print(f"[ERROR] Task failed: {data['data']}", file=sys.stderr)
                return None
            else:
                # Still processing
                print(f"   [WAIT] state={state}, elapsed={elapsed}s...", file=sys.stderr)

        except requests.RequestException as e:
            print(f"[WARN] Poll request failed: {e}", file=sys.stderr)

        time.sleep(poll_interval)
        elapsed += poll_interval

    print(f"[ERROR] Timeout after {max_time}s", file=sys.stderr)
    return None


def download_markdown(markdown_url: str) -> Optional[str]:
    """Download markdown content from CDN URL."""
    try:
        resp = requests.get(markdown_url, timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"[ERROR] Failed to download markdown: {e}", file=sys.stderr)
        return None


# === Filename utilities ===

def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Create a safe filename from a string."""
    # Remove/replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) > max_len:
        name = name[:max_len].strip()
    return name or "mineru_output"


# === Main workflow ===

def parse_url(url: str, output_file: Optional[str] = None, output_dir: Optional[str] = None,
              poll_interval: int = DEFAULT_POLL_INTERVAL, language: str = "ch") -> bool:
    """Full workflow: submit → poll → download → save."""
    print(f"[MinerU] Submitting: {url}")

    # 1. Submit
    task_id = submit_url_task(url, language=language)
    if not task_id:
        return False
    print(f"   [OK] task_id: {task_id}")

    # 2. Poll
    print(f"   [WAIT] Polling (interval={poll_interval}s, max={MAX_POLL_TIME}s)...")
    result = poll_task_status(task_id, poll_interval=poll_interval)
    if not result:
        return False

    markdown_url = result.get("markdown_url")
    if not markdown_url:
        print("[ERROR] No markdown_url in result", file=sys.stderr)
        return False
    print(f"   [OK] markdown_url: {markdown_url[:80]}...")

    # 3. Download
    content = download_markdown(markdown_url)
    if not content:
        return False
    print(f"   [OK] Content: {len(content)} chars")

    # 4. Determine output path
    if output_file:
        out_path = output_file
    else:
        # Derive filename from URL
        basename = os.path.splitext(os.path.basename(url.split('?')[0]))[0]
        if not basename or basename == '':
            basename = "mineru_output"
        basename = sanitize_filename(basename)
        out_dir = output_dir or "."
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{basename}.md")

    # 5. Save
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"   [OK] Saved: {out_path}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Parse PDF/documents via MinerU Agent API"
    )
    parser.add_argument("url", help="URL of the document to parse (PDF, DOCX, etc.)")
    parser.add_argument("-o", "--output", help="Output file path")
    parser.add_argument("-d", "--dir", help="Output directory (default: current)")
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL,
                        help=f"Polling interval in seconds (default: {DEFAULT_POLL_INTERVAL})")
    parser.add_argument("--language", default="ch",
                        help="OCR language hint (default: ch)")
    args = parser.parse_args()

    success = parse_url(
        url=args.url,
        output_file=args.output,
        output_dir=args.dir,
        poll_interval=args.poll_interval,
        language=args.language,
    )

    if success:
        print("\n[Done] Success")
    else:
        print("\n[Done] Failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
