"""Shared editorial rules for candidate preparation and the reading interface."""

import re
from typing import Literal

from .collectors import is_ai

Section = Literal["industry", "products"]
SECTIONS = {
    "industry": {
        "title": "行业热点",
        "description": "发生了什么变化，为什么值得知道",
        "reason_label": "为什么重要",
    },
    "products": {
        "title": "产品案例",
        "description": "真实产品，为谁解决什么问题，有什么新意",
        "reason_label": "新意在哪里",
    },
}
SECTION_LIMIT = 5
FORMAT_VERSION = 2

FINANCE = re.compile(
    r"\b(funding|fundrais\w*|valuation|ipo|series [a-f])\b|"
    r"\brais(?:e[sd]?|ing)\s+(?:\$|€|£|\d)|融资|募资|估值|上市募股",
    re.I,
)
PERSONNEL = re.compile(
    r"\b(layoffs?|resigns?|steps? down|appoints?|new ceo|new cto)\b|"
    r"裁员|离职|辞职|人事变动|新任(?:CEO|CTO|首席)|出任|任命",
    re.I,
)
WRAPPERS = re.compile(r"\b(chatgpt clone|another (?:ai )?chatbot)\b|同质化套壳|通用聊天套壳|换皮聊天", re.I)
LAUNCH = re.compile(r"\b(launch\w*|releas\w*|introduc\w*|open.sourc\w*)\b|发布|推出|开源|上线", re.I)
INDUSTRY = re.compile(
    r"\b(model[s]?|api|benchmark|breakthrough|context window|pricing|price cut|"
    r"weights|multimodal|foundation model)\b|"
    r"模型发布|新模型|模型能力|基础模型|推理能力|上下文|价格调整|降价|多模态|技术突破|平台规则",
    re.I,
)
PRODUCTS = re.compile(
    r"\b(show hn|app|application|product|tool|assistant|workspace|workflow|"
    r"notebooklm|ollama|coding agent|editor|browser|copilot)\b|"
    r"应用|工具|助手|产品|工作流|演示|玩法|笔记|设计|剪辑|写作|编程|浏览器",
    re.I,
)


def section_for_entry(entry: dict) -> Section | None:
    """Preview classification only; the cloud editor makes the final content judgment."""
    title = entry["title"]
    text = title + " " + entry["summary"][:1500]
    if not is_ai(text) and entry["kind"] != "repository":
        return None
    # A headline may mention financing alongside an actual product release.
    if FINANCE.search(title) or PERSONNEL.search(title):
        if not LAUNCH.search(title):
            return None
    if WRAPPERS.search(title):
        return None
    if INDUSTRY.search(title) and not PRODUCTS.search(title):
        return "industry"
    if entry["kind"] == "repository" or PRODUCTS.search(text):
        return "products"
    return "industry"
