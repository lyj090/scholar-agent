#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zotero_launcher.py — CLI wrapper for Zotero launch detection.

Provides clean status feedback for AI agents and skills.
"""

import argparse
import json
import sys
import os

# Ensure lib/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from zotero.config import load_config
from zotero.launcher import ZoteroLauncher


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="zotero_launcher",
        description="Ensure Zotero is running and API ready"
    )
    parser.add_argument("--config", help="Path to paths.local.yaml")
    parser.add_argument("--json", action="store_true", help="Output JSON for AI agents")
    parser.add_argument("--wait", type=int, default=30, help="Max wait time in seconds (default: 30)")

    args = parser.parse_args()

    try:
        config = load_config(args.config)
        launcher = ZoteroLauncher(config)
        result = launcher.ensure_zotero_running(max_wait=args.wait)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            # Clean human-readable output
            if result["status"] == "ready":
                print(f"✅ {result['message']}")
            elif result["status"] == "started":
                print(f"🔄 {result['message']}")
            else:
                print(f"❌ {result['message']}")
                return 1

        return 0

    except Exception as e:
        error_result = {
            "status": "error",
            "message": f"Configuration error: {e}"
        }

        if args.json:
            print(json.dumps(error_result, indent=2))
        else:
            print(f"❌ {error_result['message']}")

        return 1


if __name__ == "__main__":
    sys.exit(main())