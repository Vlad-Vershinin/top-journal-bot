import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
import pytest

from journal_bot.bot import ScheduleBot
from journal_bot.casino import CasinoError, CasinoStore, multiplier, validate_init_data
from journal_bot.casino_server import create_app
from journal_bot.config import Settings, can_play


TOKEN = "123:test-token-not-a-real-bot"
OWNER = 123


def signed_data(user_id=OWNER, auth_date=None):
    fields = {"auth_date": str(int(time.time()) if auth_date is None else auth_date),
              "user": json.dumps({"id": user_id, "first_name": "Test"}), "query_id": "test"}
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_telegram_auth_rejects_tampering_expired_duplicate_and_foreign_users():
    assert validate_init_data(signed_data(), TOKEN, OWNER) == OWNER
    for raw in ("", signed_data().replace("Test", "Hacker"), signed_data(auth_date=int(time.time()) - 3601),
                signed_data(auth_date=int(time.time()) + 60), signed_data() + "&user=bad"):
        with pytest.raises(CasinoError) as error:
            validate_init_data(raw, TOKEN, OWNER)
        assert error.value.status == 401
    with pytest.raises(CasinoError) as error:
        validate_init_data(signed_data(999), TOKEN, OWNER)
    assert error.value.status == 403
    with pytest.raises(CasinoError):
        validate_init_data(signed_data(), TOKEN, None)


@pytest.mark.parametrize("reels,expected", [([0, 0, 0], 8), ([5, 5, 5], 40),
                                          ([0, 1, 0], 2), ([0, 1, 2], 0)])
def test_slot_payouts(reels, expected):
    assert multiplier(reels) == expected


def test_spins_are_server_decided_persistent_and_idempotent(tmp_path):
    path = tmp_path / "casino.sqlite3"
    store = CasinoStore(path)
    key = str(uuid4())
    assert store.state(OWNER)["balance"] == 1000
    with patch("journal_bot.casino.secrets.randbelow", side_effect=[0, 0, 0]):
        result = store.spin(OWNER, 25, key)
    assert result["payout"] == 200
    assert result["balance"] == 1175
    assert store.spin(OWNER, 25, key) == result
    restored = CasinoStore(path).state(OWNER)
    assert restored["balance"] == 1175
    assert restored["spins"] == 1
    assert restored["history"] == [result]
    with pytest.raises(CasinoError):
        store.spin(OWNER, 50, key)
    with pytest.raises(CasinoError) as error:
        store.spin(OWNER, 25, str(uuid4()))
    assert error.value.status == 429


def test_concurrent_retries_charge_once(tmp_path):
    store = CasinoStore(tmp_path / "casino.sqlite3")
    key = str(uuid4())
    with patch("journal_bot.casino.secrets.randbelow", side_effect=[0, 1, 2]):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: store.spin(OWNER, 10, key), range(4)))
    assert all(result == results[0] for result in results)
    assert store.state(OWNER)["balance"] == 990
    assert store.state(OWNER)["spins"] == 1


def test_bet_validation_insufficient_points_and_free_bonus(tmp_path):
    store = CasinoStore(tmp_path / "casino.sqlite3")
    for bet in (-10, 0, 11, True, "25"):
        with pytest.raises(CasinoError):
            store.spin(OWNER, bet, str(uuid4()))
    with patch("journal_bot.casino.time.time", return_value=100000), patch(
        "journal_bot.casino.secrets.randbelow", side_effect=[0, 1, 2]
    ):
        store.spin(OWNER, 100, str(uuid4()))
    with pytest.raises(CasinoError):
        store.refill(OWNER)
    # Empty the virtual wallet with distinct losing spins at permitted intervals.
    for i in range(9):
        with patch("journal_bot.casino.time.time", return_value=100003 + i * 3), patch(
            "journal_bot.casino.secrets.randbelow", side_effect=[0, 1, 2]
        ):
            store.spin(OWNER, 100, str(uuid4()))
    with patch("journal_bot.casino.time.time", return_value=100040):
        with pytest.raises(CasinoError):
            store.spin(OWNER, 10, str(uuid4()))
        assert store.refill(OWNER)["balance"] == 1000
    for i in range(10):
        with patch("journal_bot.casino.time.time", return_value=100050 + i * 3), patch(
            "journal_bot.casino.secrets.randbelow", side_effect=[0, 1, 2]
        ):
            store.spin(OWNER, 100, str(uuid4()))
    with patch("journal_bot.casino.time.time", return_value=100100):
        with pytest.raises(CasinoError) as error:
            store.refill(OWNER)
        assert error.value.status == 429
    with patch("journal_bot.casino.time.time", return_value=186441):
        assert store.refill(OWNER)["balance"] == 1000


def test_http_api_requires_owner_and_ignores_client_payout(tmp_path):
    settings = SimpleNamespace(casino_db_file=tmp_path / "casino.sqlite3",
                               admin_user_id=OWNER, telegram_bot_token=TOKEN)

    async def scenario():
        async with TestClient(TestServer(create_app(settings))) as client:
            response = await client.get("/")
            assert response.status == 200
            assert "PLAY ROOM" in await response.text()
            for action in ("state", "spin", "refill", "leaderboard", "history"):
                response = await client.post(f"/api/{action}", json={"init_data": signed_data(999)})
                assert response.status == 403
                response = await client.post(f"/api/{action}", json={})
                assert response.status == 401
            response = await client.post("/api/state", json={"init_data": signed_data()})
            assert (await response.json())["balance"] == 1000
            payload = {"init_data": signed_data(), "bet": 10, "request_id": str(uuid4()),
                       "balance": 999999, "payout": 999999, "reels": [5, 5, 5]}
            with patch("journal_bot.casino.secrets.randbelow", side_effect=[0, 1, 2]):
                response = await client.post("/api/spin", json=payload)
                result = await response.json()
            assert result["balance"] == 990 and result["payout"] == 0
            assert response.headers["Cache-Control"] == "no-store"
            assert "script-src" in response.headers["Content-Security-Policy"]
            response = await client.post("/api/spin", json=payload)
            assert await response.json() == result
            assert (await client.get("/assets/not-a-file")).status == 404
            assert (await client.get("/.env")).status == 404
            response = await client.post("/api/state", json={"init_data": signed_data(), "user": {"id": 999, "first_name": "Fake"}})
            assert (await response.json())["profile"]["name"] == "Test"
    asyncio.run(scenario())


def test_play_button_is_private_and_requires_https():
    bot = object.__new__(ScheduleBot)
    bot.settings = SimpleNamespace(admin_user_id=OWNER, play_url="https://games.example.test/")
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=999),
                             effective_chat=SimpleNamespace(type="private"))
    asyncio.run(bot.open_play(update, SimpleNamespace()))
    message.reply_text.assert_not_awaited()
    update.effective_user.id = OWNER
    asyncio.run(bot.open_play(update, SimpleNamespace()))
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].web_app.url == bot.settings.play_url
    bot.settings.play_url = "http://insecure.example.test"
    message.reply_text.reset_mock()
    asyncio.run(bot.open_play(update, SimpleNamespace()))
    assert "reply_markup" not in message.reply_text.await_args.kwargs
    update.effective_chat.type = "group"
    message.reply_text.reset_mock()
    asyncio.run(bot.open_play(update, SimpleNamespace()))
    assert "личном чате" in message.reply_text.await_args.args[0]


def test_invited_player_can_open_play():
    bot = object.__new__(ScheduleBot)
    bot.settings = SimpleNamespace(admin_user_id=OWNER, play_tester_ids=(456,),
                                   play_url="https://games.example.test/")
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=456),
                             effective_chat=SimpleNamespace(type="private"))
    asyncio.run(bot.open_play(update, SimpleNamespace()))
    assert message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].web_app
    assert not can_play(789, OWNER, (456,))
    assert not can_play(456, None, (456,))


def test_tester_access_is_checked_on_every_api_action(tmp_path):
    settings = SimpleNamespace(casino_db_file=tmp_path / "games.sqlite3", admin_user_id=OWNER,
                               telegram_bot_token=TOKEN, play_tester_ids=(456,))

    async def scenario():
        async with TestClient(TestServer(create_app(settings))) as client:
            for action in ("state", "leaderboard", "history"):
                response = await client.post(f"/api/{action}", json={"init_data": signed_data(456)})
                assert response.status == 200
            response = await client.post("/api/spin", json={"init_data": signed_data(456),
                                                          "bet": 10, "request_id": str(uuid4())})
            assert response.status == 200
            response = await client.post("/api/refill", json={"init_data": signed_data(456)})
            assert response.status == 409  # Authorized, but balance is too high for a refill.
            for action in ("state", "spin", "refill", "leaderboard", "history"):
                response = await client.post(f"/api/{action}", json={"init_data": signed_data(789)})
                assert response.status == 403
            settings.play_tester_ids = ()
            response = await client.post("/api/state", json={"init_data": signed_data(456)})
            assert response.status == 403
    asyncio.run(scenario())


def test_tester_ids_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("JOURNAL_USERNAME", "test")
    monkeypatch.setenv("JOURNAL_PASSWORD", "test")
    monkeypatch.setenv("PLAY_TESTER_IDS", "456, 789, ,456")
    with patch("journal_bot.config.load_dotenv"):
        assert Settings.from_env().play_tester_ids == (456, 789, 456)
        monkeypatch.setenv("PLAY_TESTER_IDS", "")
        assert Settings.from_env().play_tester_ids == ()


def test_leaderboard_uses_shared_balances_and_deterministic_ties(tmp_path):
    store = CasinoStore(tmp_path / "games.sqlite3")
    for user_id, name in ((123, "Alice"), (456, "Bob"), (789, "Carol")):
        store.update_profile({"id": user_id, "first_name": name, "photo_url": "https://t.me/i/userpic/test.jpg"})
        store.state(user_id)
    with patch("journal_bot.casino.secrets.randbelow", side_effect=[5, 5, 5]):
        store.spin(456, 25, str(uuid4()))
    board = store.leaderboard(789)
    assert [player["user_id"] for player in board["players"]] == [456, 123, 789]
    assert [player["rank"] for player in board["players"]] == [1, 2, 3]
    assert board["me"]["rank"] == store.state(789)["rank"] == 3
    assert board["total"] == 3
    assert store.state(123)["profile"]["name"] == "Alice"
    store.update_profile({"id": 123, "first_name": "Alice Updated", "photo_url": "javascript:alert(1)"})
    assert store.state(123)["profile"]["photo_url"] == ""


def test_legacy_games_migrate_without_losing_history_or_balance(tmp_path):
    path = tmp_path / "games.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE spins(request_id TEXT PRIMARY KEY,user_id INTEGER,bet INTEGER,result TEXT,created REAL)")
        db.execute("INSERT INTO spins VALUES (?,?,?,?,?)", (str(uuid4()), OWNER, 10, json.dumps({"net": -10}), 100000))
    store = CasinoStore(path)
    state = store.state(OWNER)
    assert state["balance"] == 1000
    assert state["history"][0]["mode"] == "fruit_slots"
    assert state["history"][0]["created"] == 100000
    # A normal new game still works after the old table has gained its mode column.
    with patch("journal_bot.casino.secrets.randbelow", side_effect=[0, 1, 2]):
        store.spin(OWNER, 10, str(uuid4()))
    assert CasinoStore(path).state(OWNER)["balance"] == 990


def test_history_paginates_all_games_in_reverse_order(tmp_path):
    store = CasinoStore(tmp_path / "games.sqlite3")
    for index in range(23):
        with patch("journal_bot.casino.time.time", return_value=100000 + 3 * index), patch(
            "journal_bot.casino.secrets.randbelow", side_effect=[0, 1, 2]
        ):
            store.spin(OWNER, 10, str(uuid4()))
    first = store.history(OWNER)
    second = store.history(OWNER, first["next_offset"])
    assert len(first["items"]) == 20 and len(second["items"]) == 3
    assert second["next_offset"] is None
    assert first["items"][0]["created"] > second["items"][0]["created"]
    assert all(item["mode"] == "fruit_slots" and item["net"] == -10 for item in first["items"])
    assert store.history(999)["items"] == []
    for offset in (-1, True, "0"):
        with pytest.raises(CasinoError):
            store.history(OWNER, offset)
