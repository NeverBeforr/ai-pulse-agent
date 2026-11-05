import asyncio
import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .errors import SourceFailure as SourceFailure

TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref_src"}


def public_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("只支持无登录信息的公开 HTTP/HTTPS 地址")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("不允许本机地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("不允许私网或保留地址")
    return value


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    pairs = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(pairs), ""))


class Network:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(18, connect=10),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            headers={"User-Agent": "AI-Pulse-Agent/0.1 (personal news reader)"},
        )

    async def get(self, url: str, headers: dict | None = None) -> httpx.Response:
        for _ in range(5):
            public_url(url)
            host = urlsplit(url).hostname
            addresses = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, host, None), timeout=5)
            if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ValueError("来源解析到了非公开地址")
            for attempt in range(3):
                try:
                    response = await self.client.get(url, headers=headers)
                except httpx.PoolTimeout:
                    raise
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(attempt + 1)
                    continue
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    retry_after = response.headers.get("Retry-After", "2")
                    wait = int(retry_after) if retry_after.isdigit() else 2
                    if wait > 10:
                        response.raise_for_status()
                    await asyncio.sleep(wait)
                    continue
                break
            if response.is_redirect:
                new_url = urljoin(url, response.headers["location"])
                if urlsplit(new_url).netloc != urlsplit(url).netloc:
                    headers = None
                await response.aclose()
                url = new_url
                continue
            response.raise_for_status()
            if len(response.content) > 4_000_000:
                raise ValueError("来源响应超过 4 MB")
            return response
        raise ValueError("来源重定向次数过多")

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
