from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx


APPLICATION_KEY = "6a56a5df2667e65aab73ce76d1dd737f7d1faef9c52e8b8c55ac75f565d8e8a6"


class JournalError(RuntimeError):
    """A user-facing Journal API error."""


class JournalUnavailable(JournalError):
    """Journal cannot currently serve requests, so cached data may be used."""


@dataclass(frozen=True, slots=True)
class Lesson:
    day: date
    number: int
    starts_at: str
    finishes_at: str
    subject: str
    teacher: str
    room: str

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Lesson":
        return cls(
            day=date.fromisoformat(str(item["date"])[:10]),
            number=int(item.get("lesson") or 0),
            starts_at=str(item.get("started_at") or ""),
            finishes_at=str(item.get("finished_at") or ""),
            subject=str(item.get("subject_name") or "Без названия"),
            teacher=str(item.get("teacher_name") or ""),
            room=str(item.get("room_name") or ""),
        )


class JournalClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float) -> None:
        self._username = username
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._auth_lock = asyncio.Lock()
        # The API is protected by ddos-guard and rejects the default httpx
        # user agent with an HTML 403 response. These are the same public
        # request headers used by the Journal web application.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://journal.top-academy.ru",
                "Referer": "https://journal.top-academy.ru/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0 Safari/537.36"
                ),
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        async with self._auth_lock:
            try:
                response = await self._client.post(
                    "/auth/login",
                    json={
                        "username": self._username,
                        "password": self._password,
                        "application_key": APPLICATION_KEY,
                        "id_city": None,
                    },
                )
            except httpx.RequestError as exc:
                raise JournalUnavailable("Сайт Journal сейчас недоступен") from exc
            if response.status_code in (401, 422):
                raise JournalError("Journal отклонил логин или пароль")
            if response.status_code == 403:
                raise JournalUnavailable("Сайт Journal сейчас недоступен (HTTP 403)")
            if response.status_code >= 500:
                raise JournalUnavailable(
                    f"Сайт Journal сейчас недоступен (HTTP {response.status_code})"
                )
            try:
                response.raise_for_status()
                payload = response.json()
                self._access_token = payload["access_token"]
                self._refresh_token = payload.get("refresh_token")
            except (KeyError, ValueError) as exc:
                raise JournalUnavailable("Journal вернул некорректный ответ") from exc
            except httpx.HTTPError as exc:
                raise JournalError("Не удалось авторизоваться в Journal") from exc

    async def _refresh(self) -> bool:
        if not self._refresh_token:
            return False
        try:
            response = await self._client.post(
                "/auth/refresh", json={"refresh_token": self._refresh_token}
            )
        except httpx.RequestError as exc:
            raise JournalUnavailable("Сайт Journal сейчас недоступен") from exc
        if response.status_code == 403 or response.status_code >= 500:
            raise JournalUnavailable(
                f"Сайт Journal сейчас недоступен (HTTP {response.status_code})"
            )
        if response.is_error:
            return False
        try:
            payload = response.json()
            self._access_token = payload["access_token"]
            self._refresh_token = payload.get("refresh_token", self._refresh_token)
        except (KeyError, ValueError) as exc:
            raise JournalUnavailable("Journal вернул некорректный ответ") from exc
        return True

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        if not self._access_token:
            await self._login()

        for attempt in range(2):
            try:
                response = await self._client.get(
                    path,
                    params=params,
                    headers={"Authorization": f"Bearer {self._access_token}"},
                )
            except httpx.RequestError as exc:
                raise JournalUnavailable("Сайт Journal сейчас недоступен") from exc

            if response.status_code == 401 and attempt == 0:
                if not await self._refresh():
                    await self._login()
                continue
            if response.status_code == 403 or response.status_code >= 500:
                raise JournalUnavailable(
                    f"Сайт Journal сейчас недоступен (HTTP {response.status_code})"
                )
            try:
                response.raise_for_status()
                return response.json()
            except ValueError as exc:
                raise JournalUnavailable("Journal вернул некорректный ответ") from exc
            except httpx.HTTPError as exc:
                raise JournalError(
                    f"Journal вернул ошибку HTTP {response.status_code}"
                ) from exc
        raise JournalError("Не удалось обновить авторизацию Journal")

    async def schedule_for_day(self, day: date) -> list[Lesson]:
        payload = await self._get(
            "/schedule/operations/get-by-date",
            {"date_filter": day.isoformat()},
        )
        return self._parse_lessons(payload)

    async def schedule_for_range(self, start: date, end: date) -> list[Lesson]:
        payload = await self._get(
            "/schedule/operations/get-by-date-range",
            {"date_start": start.isoformat(), "date_end": end.isoformat()},
        )
        return self._parse_lessons(payload)

    @staticmethod
    def _parse_lessons(payload: Any) -> list[Lesson]:
        if not isinstance(payload, list):
            raise JournalError("Journal вернул расписание в неизвестном формате")
        lessons = [Lesson.from_api(item) for item in payload if isinstance(item, dict)]
        return sorted(lessons, key=lambda item: (item.day, item.number, item.starts_at))
