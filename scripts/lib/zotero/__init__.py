# scripts/lib/zotero — Zotero CLI bridge package.
# Provides terminal-native access to Zotero Local API + MinerU cache.
# No AI dependencies; designed for pipe-friendly CLI usage.

from .config import ZoteroConfig, load_config
from .api import ZoteroAPI
from .mineru_catalog import MineruCatalog, MineruRecord, MineruSection
from .mineru_lookup import MineruLookup

__all__ = [
    "ZoteroConfig",
    "load_config",
    "ZoteroAPI",
    "MineruCatalog",
    "MineruRecord",
    "MineruSection",
    "MineruLookup",
]
