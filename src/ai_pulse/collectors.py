"""旧导入路径兼容层；采集实现位于sources包。"""

from .sources.common import AI_TERMS, clean_text, is_ai
from .sources.legacy import Collectors
from .sources.rss import parse_feed, parse_media
from .sources.tldr import parse_tldr

__all__ = ["AI_TERMS", "clean_text", "is_ai", "parse_feed", "parse_media", "parse_tldr", "Collectors"]
