#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — Configuration loader for arXiv workflow.

Loads and validates YAML configuration for keyword groups, output settings, etc.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# Default configuration
DEFAULT_CONFIG = {
    "keywords": {
        "Manipulation": {
            "filters": ["Robot Manipulation", "Robotic Manipulation", "Manipulation Policy"]
        },
        "VLM": {"filters": ["Vision Language Model", "Vision-Language Model"]},
        "VLA": {"filters": ["Vision Language Action", "Vision-Language-Action"]},
    },
    "output": {
        "json_dir": "./data",
        "readme_path": "docs/arxiv/daily-readme.md",
        "pages_path": "./docs/arxiv/pages-index.md",
        "wechat_path": "./docs/wechat.md",
        "formats": {"readme": True, "github_pages": True, "wechat": False},
    },
    "api": {
        "max_retries": 10,
        "delay_seconds": 3.0,
        "rate_limit_delay": 30,
        "timeout": 30,
        "batch_size": 20,
    },
    "publish": {"auto_publish": False, "repo_url": "", "branch": "main", "commit_template": "Update papers: {date}"},
    "display": {
        "title_max_len": 60,
        "authors_max_len": 30,
        "default_top_n": 15,
        "show_score": True,
        "show_code_links": True,
    },
}


class ConfigLoader:
    """Loads and provides access to arXiv workflow configuration."""

    def __init__(self, config_path: str | None = None):
        """Initialize config loader.

        Args:
            config_path: Path to config.yaml. If None, searches default locations.
        """
        self._config: dict[str, Any] = {}
        self._config_path: str | None = None

        if config_path:
            self._config_path = config_path
        else:
            # Search default locations
            default_paths = [
                os.path.join(os.path.dirname(__file__), "config.yaml"),
                os.path.join(os.path.dirname(__file__), "..", "..", "..", "config.yaml"),
                os.path.expanduser("~/.arxiv_workflow/config.yaml"),
            ]
            for p in default_paths:
                if os.path.exists(p):
                    self._config_path = p
                    break

    def Load(self) -> bool:
        """Load configuration from YAML file.

        Returns:
            True if loaded successfully, False otherwise.
        """
        if not self._config_path or not os.path.exists(self._config_path):
            print(f"[Config] No config file found, using defaults.")
            self._config = DEFAULT_CONFIG.copy()
            return False

        if not _HAS_YAML:
            print(f"[Config] PyYAML not installed, using defaults. Install with: pip install pyyaml")
            self._config = DEFAULT_CONFIG.copy()
            return False

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f)
            # Merge with defaults (in case some keys are missing)
            self._config = self._MergeWithDefaults(self._config)
            print(f"[Config] Loaded from {self._config_path}")
            return True
        except Exception as e:
            print(f"[Config] Failed to load config: {e}. Using defaults.")
            self._config = DEFAULT_CONFIG.copy()
            return False

    def GetKeywords(self) -> dict[str, list[str]]:
        """Get keyword groups.

        Returns:
            Dict mapping group name to list of filter keywords.
        """
        keywords = self._config.get("keywords", {})
        result = {}
        for name, data in keywords.items():
            if isinstance(data, dict) and "filters" in data:
                result[name] = data["filters"]
            elif isinstance(data, list):
                result[name] = data
        return result

    def GetOutputSettings(self) -> dict:
        """Get output settings."""
        return self._config.get("output", DEFAULT_CONFIG["output"])

    def GetApiSettings(self) -> dict:
        """Get API settings."""
        return self._config.get("api", DEFAULT_CONFIG["api"])

    def GetPublishSettings(self) -> dict:
        """Get publish settings."""
        return self._config.get("publish", DEFAULT_CONFIG["publish"])

    def GetDisplaySettings(self) -> dict:
        """Get display settings."""
        return self._config.get("display", DEFAULT_CONFIG["display"])

    def Get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key (supports dot notation).

        Args:
            key: Configuration key (e.g., "api.max_retries").
            default: Default value if key not found.

        Returns:
            Configuration value or default.
        """
        parts = key.split(".")
        value = self._config
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    @staticmethod
    def _MergeWithDefaults(config: dict) -> dict:
        """Merge user config with defaults."""

        def _MergeDict(default: dict, user: dict) -> dict:
            result = default.copy()
            for key, value in user.items():
                if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = _MergeDict(result[key], value)
                else:
                    result[key] = value
            return result

        return _MergeDict(DEFAULT_CONFIG.copy(), config)


def LoadConfig(config_path: str | None = None) -> ConfigLoader:
    """Convenience function to load configuration.

    Args:
        config_path: Path to config.yaml.

    Returns:
        ConfigLoader instance with loaded configuration.
    """
    loader = ConfigLoader(config_path)
    loader.Load()
    return loader
