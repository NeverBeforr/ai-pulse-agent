from ..errors import ConfigError
from .feishu import FeishuSink
from .markdown import MarkdownSink

SINKS = {"feishu": FeishuSink, "markdown": MarkdownSink}


def create_sink(config):
    if config.type not in SINKS:
        raise ConfigError(f"未注册输出类型{config.type}；可用：{', '.join(SINKS)}")
    return SINKS[config.type](config)
