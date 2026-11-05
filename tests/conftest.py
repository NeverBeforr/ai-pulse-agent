"""测试只使用临时状态、显式假凭据和离线传输。"""

import imaplib
import logging
import os
import tempfile
from pathlib import Path

import httpx
import pytest

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pulse-tests-")


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    monkeypatch.setattr(logging.getLogger("ai_pulse"), "handlers", [])
    monkeypatch.setattr(logging.getLogger("ai_pulse"), "propagate", True)

    async def deny_async(self, request):
        raise AssertionError("测试禁止真实网络请求")

    def deny_sync(self, request):
        raise AssertionError("测试禁止真实网络请求")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_sync)
    monkeypatch.setattr(
        imaplib, "IMAP4_SSL", lambda *a, **k: (_ for _ in ()).throw(AssertionError("测试禁止真实IMAP连接"))
    )
    for key in (
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "FEISHU_WEBHOOK",
        "IMAP_USER",
        "IMAP_PASSWORD",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "PULSE_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def pulse_config(tmp_path, monkeypatch):
    from ai_pulse.config import load_config

    path = tmp_path / "pulse.yaml"
    path.write_text(
        (Path(__file__).parent / "fixtures/pulse.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for key, value in {
        "LLM_BASE_URL": "https://example.com/v1",
        "LLM_MODEL": "fake-model",
        "LLM_API_KEY": "fake-model-secret",
        "FEISHU_WEBHOOK": "https://example.com/bot/fake-webhook-secret",
    }.items():
        monkeypatch.setenv(key, value)
    return load_config(path)
