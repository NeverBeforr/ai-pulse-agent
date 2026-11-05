"""来源适配器；保留旧看板的默认来源导入。"""

from .base import SOURCES, Source, create_source, register
from .defaults import default_sources
from .hn import HNSource
from .imap import IMAPSource
from .rss import RSSSource
from .tldr import TLDRSource

__all__ = [
    "SOURCES",
    "Source",
    "create_source",
    "register",
    "default_sources",
    "HNSource",
    "IMAPSource",
    "RSSSource",
    "TLDRSource",
]
