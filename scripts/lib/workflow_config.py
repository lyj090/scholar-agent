"""One private YAML/JSON configuration for the literature workflow.

Only this module reads personal configuration. Public examples contain no
account identifiers, project vocabulary, or credentials. Credentials stay in
environment variables / the platform's secret store.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "shared/paths.local.yaml"


def config_path() -> Path:
    return Path(os.environ.get("PAPER_WORKFLOW_CONFIG", str(DEFAULT_CONFIG))).expanduser()


def load_config(path: str | Path | None = None) -> dict:
    source = Path(path).expanduser() if path else config_path()
    if not source.exists():
        return {}
    try:
        if source.suffix == ".json":
            data = json.loads(source.read_text(encoding="utf-8"))
        else:
            import yaml
            try:
                data = yaml.safe_load(source.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ValueError("Invalid private workflow configuration") from exc
    except (ValueError, OSError) as exc:
        raise ValueError("Invalid private workflow configuration") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Workflow configuration must be a mapping")
    return data


def section(name: str) -> dict:
    data = load_config().get(name, {})
    if not isinstance(data, dict):
        raise ValueError(f"Configuration section {name} must be a mapping")
    return data


def private_terms() -> tuple[str, ...]:
    values = section("privacy").get("terms", [])
    if not isinstance(values, list) or any(not isinstance(t, str) or not t.strip() for t in values):
        raise ValueError("privacy.terms must be a list of non-empty strings")
    return tuple(values)


def merge_taxonomy(data: dict) -> dict:
    """Extend the public vocabulary with private mappings without publishing it."""
    local = section("tagging").get("mapping", {})
    if not isinstance(local, dict):
        raise ValueError("tagging.mapping must be a mapping")
    data["mapping"] = {**(data.get("mapping") or {}), **local}
    return data
