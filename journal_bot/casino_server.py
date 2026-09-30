from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web

from .casino import CasinoError, CasinoStore, validated_user
from .config import Settings
from .logging_setup import configure_logging


STATIC = Path(__file__).with_name("casino_web")


def create_app(settings: Settings) -> web.Application:
    store = CasinoStore(settings.casino_db_file)

    @web.middleware
    async def headers(request: web.Request, handler):
        try:
            response = await handler(request)
        except CasinoError as exc:
            response = web.json_response({"error": str(exc)}, status=exc.status)
        except web.HTTPException as exc:
            response = web.Response(status=exc.status, text=exc.text, content_type=exc.content_type)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://telegram.org; "
            "style-src 'self'; img-src 'self' data: https:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; "
            "frame-ancestors https://web.telegram.org https://*.telegram.org"
        )
        return response

    async def api(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            raise CasinoError("Ожидается JSON.", 415)
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise CasinoError("Некорректный запрос.") from None
        if not isinstance(body, dict):
            raise CasinoError("Некорректный запрос.")
        user = validated_user(
            body.get("init_data"), settings.telegram_bot_token, settings.admin_user_id
        )
        user_id = user["id"]
        await asyncio.to_thread(store.update_profile, user)
        action = request.match_info["action"]
        if action == "state":
            result = await asyncio.to_thread(store.state, user_id)
        elif action == "spin":
            result = await asyncio.to_thread(store.spin, user_id, body.get("bet"), body.get("request_id"))
        elif action == "refill":
            result = await asyncio.to_thread(store.refill, user_id)
        elif action == "leaderboard":
            result = await asyncio.to_thread(store.leaderboard, user_id)
        elif action == "history":
            result = await asyncio.to_thread(store.history, user_id, body.get("offset", 0))
        else:
            raise web.HTTPNotFound()
        return web.json_response(result)

    async def page(request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC / "index.html")

    async def asset(request: web.Request) -> web.FileResponse:
        name = request.match_info["name"]
        if name not in {"app.js", "style.css"}:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / name)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[headers], client_max_size=16384)
    app.router.add_get("/", page)
    app.router.add_get("/assets/{name}", asset)
    app.router.add_get("/health", health)
    app.router.add_post("/api/{action}", api)
    return app


def main() -> None:
    settings = Settings.from_env()
    if settings.admin_user_id is None:
        raise ValueError("Для Mini App заполните ADMIN_TELEGRAM_USER_ID")
    configure_logging(settings.log_dir / "casino", settings.log_level)
    logging.getLogger(__name__).info("Private mini games app is starting")
    web.run_app(create_app(settings), host="0.0.0.0", port=int(os.getenv("CASINO_PORT", "8080")), access_log=None)


if __name__ == "__main__":
    main()
