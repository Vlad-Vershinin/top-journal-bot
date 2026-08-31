import asyncio
from datetime import date

import httpx

from journal_bot.journal import JournalClient


def test_full_login_after_rejected_refreshed_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        schedule_calls = calls.count("/schedule/operations/get-by-date")
        if request.url.path == "/auth/refresh":
            return httpx.Response(
                200,
                json={"access_token": "refreshed", "refresh_token": "refresh-2"},
            )
        if request.url.path == "/auth/login":
            return httpx.Response(
                200,
                json={"access_token": "new-login", "refresh_token": "refresh-3"},
            )
        if schedule_calls < 3:
            return httpx.Response(401, json={"message": "Unauthorized"})
        return httpx.Response(200, json=[])

    async def run() -> None:
        journal = JournalClient("https://journal.test", "user", "password", 5)
        await journal._client.aclose()
        journal._client = httpx.AsyncClient(
            base_url="https://journal.test",
            transport=httpx.MockTransport(handler),
        )
        journal._access_token = "expired"
        journal._refresh_token = "refresh-1"
        try:
            assert await journal.schedule_for_day(date(2026, 9, 1)) == []
        finally:
            await journal.close()

    asyncio.run(run())
    assert calls == [
        "/schedule/operations/get-by-date",
        "/auth/refresh",
        "/schedule/operations/get-by-date",
        "/auth/login",
        "/schedule/operations/get-by-date",
    ]
