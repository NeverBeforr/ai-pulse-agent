import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from ..errors import DeliveryError
from .base import coverage_line


def render_card(digest, items, feedback_url=None, authentication=None):
    elements = []
    for item in items:
        title = item["title"].replace("[", "（").replace("]", "）")
        content = f"**[{title}]({item['url']})**\n{item['summary'][:140]}\n理由：{item['reason']}"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
    clock = (
        datetime.fromisoformat(digest["created_at"])
        .astimezone(ZoneInfo(digest["timezone"]))
        .strftime("%H:%M")
    )
    note = coverage_line(digest) + f" · 生成 {clock}"
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    if feedback_url:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"[反馈]({feedback_url})"}})
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {
                    "tag": "plain_text",
                    "content": f"Pulse · {digest['local_date']} · {len(items)}条中文简报",
                },
            },
            "elements": elements,
        },
    }
    card.update(authentication or {})
    if len(json.dumps(card, ensure_ascii=False).encode("utf-8")) >= 20 * 1024:
        raise DeliveryError("飞书消息超过20KB，未发送；请缩短标题、链接或条目数量")
    return card


class FeishuSink:
    type = "feishu"

    def __init__(self, config):
        self.webhook = config.webhook.get_secret_value()
        self.feedback_url = config.feedback_url
        self.signing_secret = config.signing_secret.get_secret_value() if config.signing_secret else None

    async def send(self, digest, items):
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            for attempt in range(3):
                authentication = {}
                if self.signing_secret:
                    timestamp = str(int(time.time()))
                    # 飞书使用时间戳与密钥组成HMAC的key，对空消息签名。
                    key = f"{timestamp}\n{self.signing_secret}".encode("utf-8")
                    signature = base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode(
                        "ascii"
                    )
                    authentication = {"timestamp": timestamp, "sign": signature}
                payload = render_card(digest, items, self.feedback_url, authentication)
                try:
                    response = await client.post(self.webhook, json=payload)
                except httpx.RequestError as exc:
                    if attempt == 2:
                        raise DeliveryError(f"飞书网络错误：{type(exc).__name__}") from exc
                    await asyncio.sleep((2, 6)[attempt])
                    continue
                if response.status_code >= 400 or response.is_redirect:
                    raise DeliveryError(f"飞书HTTP {response.status_code}，没有确认送达")
                try:
                    result = response.json()
                except ValueError as exc:
                    raise DeliveryError("飞书返回的不是JSON确认信息") from exc
                code = result.get("code", result.get("StatusCode"))
                if code != 0:
                    detail = {
                        19021: "签名不匹配或时间戳过期",
                        19022: "机器人IP白名单拒绝了来源",
                        19024: "消息未满足机器人关键词校验",
                    }.get(code, "没有确认送达")
                    raise DeliveryError(f"飞书没有接受消息，业务码：{code}；{detail}")
                return
