#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
launcher.py — Zotero launch detection and auto-start.

Why this exists
---------------
The native write API only answers while Zotero is running, so every write
command (``write status``, ``import``, ``mineru-push``, ...) fails whenever the
user has closed the app. The launcher makes that recoverable instead of
terminal: it probes, starts Zotero when needed, and waits for the API to answer.

Two detection traps this module exists to absorb:

1. **``pgrep -f zotero`` matches the caller itself.** The pattern is matched
   against full command lines, and the probing process's own command line
   usually contains the word "zotero" (a script path or an argument). Measured
   on this machine: a Python process making exactly that call saw its own PID
   listed as a Zotero process. The result is a launcher that reports "process
   running, API not ready" and waits out its whole timeout instead of starting
   the editor. Detection is therefore anchored to the *executable name* via
   ``/proc`` (or ``pgrep -x``), never a substring of a command line.

2. **The port is not a constant to hardcode in two places.** ``api_url`` is
   configurable; probing 23119 regardless would report the wrong thing on a
   machine that moved the port. The port is parsed from the configured URL.

NOT responsible for: HTTP write semantics (``local_write``), config discovery
(``config``).
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time
from typing import Optional
from urllib.parse import urlparse

from .api import ZoteroAPI
from .config import ZoteroConfig, load_config

#: Process names a real Zotero instance runs under. On Linux the launcher shell
#: script is ``zotero`` and the application itself is ``zotero-bin``; macOS and
#: Windows report ``zotero`` / ``zotero.exe``.
_PROCESS_NAMES = ("zotero", "zotero-bin", "zotero.exe")

#: Candidate binaries, tried in order, for the platforms this repo supports.
_BINARY_CANDIDATES = (
    "/usr/bin/zotero",
    "/usr/lib/zotero/zotero",
    "zotero",
    "/Applications/Zotero.app/Contents/MacOS/zotero",
)


def _running_pids() -> list[int]:
    """PIDs of live Zotero processes, excluding the caller.

    Reads ``/proc`` where available because it is exact: the check is on the
    executable's basename, so a process that merely *mentions* zotero in its
    arguments can never be mistaken for the editor. Falls back to ``pgrep -x``
    (exact-name match) elsewhere.
    """
    pids: list[int] = []
    if os.path.isdir("/proc"):
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid == os.getpid():
                continue
            try:
                exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
            except OSError:
                # Kernel threads and processes we may not inspect: fall back to
                # comm, which is still an exact name (never a substring).
                try:
                    with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as handle:
                        exe = handle.read().strip()
                except OSError:
                    continue
            if exe in _PROCESS_NAMES:
                pids.append(pid)
        return pids

    for name in _PROCESS_NAMES:
        try:
            result = subprocess.run(
                ["pgrep", "-x", name], capture_output=True, text=True
            )
        except OSError:
            continue
        if result.returncode == 0:
            pids.extend(
                int(value) for value in result.stdout.split() if value.isdigit()
            )
    return pids


class ZoteroLauncher:
    """Handles Zotero process detection and auto-start."""

    def __init__(self, config: ZoteroConfig):
        self.config = config
        self.api = ZoteroAPI(config.api_url, config.user_id)
        parsed = urlparse(config.api_url)
        self._port = parsed.port or (443 if parsed.scheme == "https" else 23119)
        self._host = parsed.hostname or "127.0.0.1"

    # ------------------------------------------------------------------
    #  Detection
    # ------------------------------------------------------------------

    def is_zotero_running(self) -> bool:
        """True when a Zotero process is alive.

        Exact-name matching only — see the module docstring for the measured
        self-match a substring search produces.
        """
        try:
            return bool(_running_pids())
        except Exception:
            return False

    def is_zotero_ready(self) -> bool:
        """True when the local API answers."""
        try:
            return self.api.check_connection()
        except Exception:
            return False

    def _is_port_listening(self) -> bool:
        """True when the configured API port accepts a connection."""
        import socket

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                return sock.connect_ex((self._host, self._port)) == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    #  Start
    # ------------------------------------------------------------------

    def _find_binary(self) -> str | None:
        """Locate a Zotero executable, or None when none is installed."""
        for candidate in _BINARY_CANDIDATES:
            if os.path.isabs(candidate):
                if os.path.exists(candidate):
                    return candidate
            else:
                found = shutil.which(candidate)
                if found:
                    return found

        # A desktop entry can point at an install location the fixed list misses
        # (Flatpak exports, a tarball unpacked under $HOME).
        for entry in glob.glob(
            os.path.expanduser("~/.local/share/applications/*zotero*.desktop")
        ):
            try:
                with open(entry, "r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.startswith("Exec="):
                            continue
                        command = re.sub(
                            r"\s+%[fFuUdDnNickvm]", "", line[len("Exec=") :].strip()
                        )
                        binary = command.split()[0] if command else ""
                        if binary and (shutil.which(binary) or os.path.exists(binary)):
                            return binary
            except OSError:
                continue
        return None

    def start_zotero(self) -> bool:
        """Start Zotero detached. Returns False when it cannot be launched."""
        binary = self._find_binary()
        if not binary:
            return False
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    #  Ensure
    # ------------------------------------------------------------------

    def ensure_zotero_running(self, max_wait: int = 60) -> dict:
        """Ensure Zotero is running and its API is responsive.

        Returns a status dict: ``ready`` (already up, or came up while waiting),
        ``started`` (launched and became ready), or ``error`` with a diagnostic.
        """
        if self.is_zotero_ready():
            return {
                "status": "ready",
                "message": "Zotero is running and API is responsive",
            }

        process_running = self.is_zotero_running()
        if process_running:
            for elapsed in range(max_wait):
                if self.is_zotero_ready():
                    return {
                        "status": "ready",
                        "message": f"Zotero was running, API ready after {elapsed + 1}s",
                    }
                time.sleep(1)
            return {
                "status": "error",
                "message": f"Zotero process running but API not ready after {max_wait}s",
                "diagnostic": (
                    f"Process check: {process_running}, "
                    f"Port {self._port} listening: {self._is_port_listening()}, "
                    "API check failed. Enable 'Allow other applications to "
                    "communicate with Zotero' in Zotero's Advanced settings."
                ),
            }

        if not self.start_zotero():
            return {
                "status": "error",
                "message": "Zotero is not running and could not be started",
                "diagnostic": (
                    "No Zotero executable found. Install Zotero or start it "
                    "manually, then retry."
                ),
            }

        for elapsed in range(max_wait):
            if self.is_zotero_ready():
                return {
                    "status": "started",
                    "message": f"Zotero started and API ready after {elapsed + 1}s",
                }
            # A process that dies during startup is a different failure from one
            # that is merely slow; report the first one early.
            if elapsed % 5 == 0 and not self.is_zotero_running():
                return {
                    "status": "error",
                    "message": f"Zotero process died during startup after {elapsed + 1}s",
                    "diagnostic": "Process died during startup",
                }
            time.sleep(1)

        return {
            "status": "error",
            "message": f"Zotero started but API not ready after {max_wait}s",
            "diagnostic": (
                f"Process check: {self.is_zotero_running()}, "
                f"Port {self._port} listening: {self._is_port_listening()}, "
                "API check failed after start"
            ),
        }

    def probe(self) -> dict:
        """Report detection state without starting anything."""
        return {
            "running": self.is_zotero_running(),
            "ready": self.is_zotero_ready(),
            "port": self._port,
            "port_listening": self._is_port_listening(),
            "binary": self._find_binary(),
        }


def ensure_zotero(config_path: Optional[str] = None, max_wait: int = 60) -> dict:
    """Convenience wrapper: load config and ensure Zotero is usable."""
    try:
        config = load_config(config_path)
        return ZoteroLauncher(config).ensure_zotero_running(max_wait=max_wait)
    except Exception as error:
        return {
            "status": "error",
            "message": f"Configuration error: {error}",
            "diagnostic": "Check paths.local.yaml and Zotero discovery",
        }
