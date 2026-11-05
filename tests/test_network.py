import httpx

from ai_pulse.network import Network


async def test_redirect_response_is_closed_and_run_client_is_closed(monkeypatch):
    calls = []

    class Redirect:
        status_code = 302
        is_redirect = True
        headers = {"location": "https://example.org/final"}

        async def aclose(self):
            calls.append("关闭重定向")

    class Client:
        def __init__(self, **kwargs):
            pass

        async def get(self, url, headers):
            calls.append((url, headers))
            if len([c for c in calls if isinstance(c, tuple)]) == 1:
                return Redirect()
            return httpx.Response(200, text="done", request=httpx.Request("GET", url))

        async def aclose(self):
            calls.append("关闭客户端")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        "ai_pulse.network.socket.getaddrinfo", lambda *args: [(2, 1, 6, "", ("8.8.8.8", 443))]
    )
    async with Network() as network:
        assert (await network.get("https://example.com/start", {"Authorization": "private"})).text == "done"
    assert "关闭重定向" in calls and calls[-1] == "关闭客户端"
    assert next(c for c in calls if isinstance(c, tuple) and "example.org" in c[0])[1] is None
