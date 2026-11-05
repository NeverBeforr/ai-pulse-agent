import re

from bs4 import BeautifulSoup

AI_TERMS = re.compile(
    r"(?<![A-Za-z0-9_])(ai|llm|gpt|claude|gemini|deepseek|qwen|agent[s]?|rag|ollama|vllm|"
    r"openai|anthropic|chatgpt|transformer[s]?|diffusion|inference|machine learning)(?![A-Za-z0-9_])"
    r"|人工智能|大模型|智能体|生成式|模型推理|模型\s*API|多模态|量子位|通义|智谱|豆包",
    re.I,
)


def is_ai(text: str) -> bool:
    return bool(AI_TERMS.search(text))


def clean_text(html: str, limit: int = 3500) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:limit]
