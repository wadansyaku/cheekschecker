import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import watch_cheeks


class _DummyResponse:
    def __init__(self, text: str = "<html><body>ok</body></html>"):
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


def test_fetch_calendar_html_falls_back_to_requests(monkeypatch):
    """Playwright が使えない場合でも requests で取得にフォールバックすることを確認する。"""

    class _FailingPlaywrightContext:
        async def __aenter__(self):
            raise RuntimeError("no playwright browser")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def failing_playwright():
        return _FailingPlaywrightContext()

    calls = {}

    def fake_get(url, headers=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["timeout"] = timeout
        return _DummyResponse()

    monkeypatch.setattr(watch_cheeks, "async_playwright", failing_playwright)
    monkeypatch.setattr(watch_cheeks.requests, "get", fake_get)

    settings = replace(
        watch_cheeks.load_settings(),
        target_url="http://example.com",
        ua_contact="contact@example.com",
    )

    html, source = asyncio.run(watch_cheeks.fetch_calendar_html(settings))

    assert html.startswith("<html>")
    assert source == "http://example.com"
    assert calls["url"] == "http://example.com"
    assert "User-Agent" in calls["headers"]
    assert calls["timeout"] == 30
