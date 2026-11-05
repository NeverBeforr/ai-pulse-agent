"""信息源协议与显式注册表。"""

from datetime import datetime
from typing import Protocol

from ..errors import ConfigError
from ..models import CollectedItem
from ..network import Network


class Source(Protocol):
    type: str

    def __init__(self, config: dict, network: Network): ...

    async def fetch(self, since: datetime) -> list[CollectedItem]: ...


SOURCES: dict[str, type[Source]] = {}


def register(name: str):
    def add(adapter):
        SOURCES[name] = adapter
        return adapter

    return add


def create_source(config, network):
    if config.type not in SOURCES:
        raise ConfigError(f"未注册来源类型{config.type}；已注册：{', '.join(sorted(SOURCES))}")
    return SOURCES[config.type](config.model_dump(), network)
