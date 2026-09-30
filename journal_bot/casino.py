from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl
from urllib.parse import urlsplit
from typing import Iterator
from uuid import UUID


SYMBOLS = ("🍌", "🍒", "🍋", "🍇", "🍉", "⭐")
TRIPLES = (8, 10, 12, 15, 20, 40)
BETS = (10, 25, 50, 100)
START_BALANCE = 1000
MODES = ({"id": "fruit_slots", "name": "Фруктовый микс", "description": "Собери комбинацию на трёх барабанах", "available": True},)


class CasinoError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validated_user(raw: str, token: str, owner: int | None, now: float | None = None) -> dict:
    """Validate Telegram's HMAC before using the user ID (never initDataUnsafe)."""
    if owner is None or not isinstance(raw, str) or not raw or len(raw) > 8192:
        raise CasinoError("Откройте мини-игры через /play в Telegram.", 401)
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError("Duplicate fields")
        supplied = fields.pop("hash")
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("Invalid signature")
        clock = time.time() if now is None else now
        age = clock - int(fields["auth_date"])
        if age < -30 or age > 3600:
            raise ValueError("Expired")
        user = json.loads(fields["user"])
        user_id = user["id"]
        if type(user_id) is not int:
            raise ValueError("Invalid user ID")
    except (KeyError, ValueError, TypeError):
        raise CasinoError("Сессия недействительна. Заново откройте /play.", 401) from None
    if user_id != owner:
        raise CasinoError("Игра пока доступна только владельцу бота.", 403)
    return user


def validate_init_data(raw: str, token: str, owner: int | None, now: float | None = None) -> int:
    return validated_user(raw, token, owner, now)["id"]


def multiplier(reels: list[int]) -> int:
    if len(set(reels)) == 1:
        return TRIPLES[reels[0]]
    return 2 if len(set(reels)) == 2 else 0


class CasinoStore:
    """Server-side virtual points and atomic, idempotent spins."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS wallets (
                    user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL CHECK(balance >= 0),
                    spins INTEGER NOT NULL DEFAULT 0, won INTEGER NOT NULL DEFAULT 0,
                    last_spin REAL NOT NULL DEFAULT 0, last_refill REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS spins (
                    request_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                    bet INTEGER NOT NULL, result TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    username TEXT, photo_url TEXT
                );
            """)
            if "mode" not in {row["name"] for row in db.execute("PRAGMA table_info(spins)")}:
                db.execute("ALTER TABLE spins ADD COLUMN mode TEXT NOT NULL DEFAULT 'fruit_slots'")
            db.execute("CREATE INDEX IF NOT EXISTS spins_user_time ON spins(user_id,created DESC,request_id)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def update_profile(self, user: dict) -> None:
        name = " ".join(
            str(user.get(field) or "").strip() for field in ("first_name", "last_name")
        ).strip()[:128] or "Игрок"
        username = str(user.get("username") or "")[:64]
        photo = user.get("photo_url")
        if not isinstance(photo, str) or len(photo) > 2048:
            photo = ""
        try:
            parsed = urlsplit(photo)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                photo = ""
        except ValueError:
            photo = ""
        with self._connect() as db:
            db.execute(
                "INSERT INTO profiles VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "name=excluded.name,username=excluded.username,photo_url=excluded.photo_url",
                (user["id"], name, username, photo),
            )

    @staticmethod
    def _history_item(row: sqlite3.Row) -> dict:
        result = json.loads(row["result"])
        result.update(mode=row["mode"], created=row["created"])
        return result

    @staticmethod
    def _profile(db: sqlite3.Connection, user_id: int) -> dict:
        row = db.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else {"user_id": user_id, "name": "Игрок", "username": "", "photo_url": ""}

    @staticmethod
    def _rank(db: sqlite3.Connection, wallet: sqlite3.Row) -> int:
        return 1 + db.execute(
            "SELECT COUNT(*) FROM wallets WHERE balance>? OR (balance=? AND user_id<?)",
            (wallet["balance"], wallet["balance"], wallet["user_id"]),
        ).fetchone()[0]

    @staticmethod
    def _wallet(db: sqlite3.Connection, user_id: int) -> sqlite3.Row:
        db.execute("INSERT OR IGNORE INTO wallets(user_id,balance) VALUES (?,?)", (user_id, START_BALANCE))
        return db.execute("SELECT * FROM wallets WHERE user_id=?", (user_id,)).fetchone()

    def state(self, user_id: int) -> dict:
        with self._connect() as db:
            wallet = self._wallet(db, user_id)
            history = db.execute(
                "SELECT * FROM spins WHERE user_id=? ORDER BY created DESC,request_id DESC LIMIT 10", (user_id,)
            ).fetchall()
            profile = self._profile(db, user_id)
            rank = self._rank(db, wallet)
        return {
            "balance": wallet["balance"], "spins": wallet["spins"], "won": wallet["won"],
            "refill_at": wallet["last_refill"] + 86400 if wallet["last_refill"] else 0,
            "symbols": SYMBOLS, "triples": TRIPLES, "bets": BETS,
            "history": [self._history_item(row) for row in history],
            "profile": profile, "rank": rank, "modes": MODES,
        }

    def leaderboard(self, user_id: int) -> dict:
        with self._connect() as db:
            wallet = self._wallet(db, user_id)
            rows = db.execute(
                "SELECT user_id,balance FROM wallets ORDER BY balance DESC,user_id ASC LIMIT 100"
            ).fetchall()
            players = [{**self._profile(db, row["user_id"]), "balance": row["balance"], "rank": index}
                       for index, row in enumerate(rows, start=1)]
            me = {**self._profile(db, user_id), "balance": wallet["balance"], "rank": self._rank(db, wallet)}
            count = db.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
        return {"players": players, "me": me, "total": count}

    def history(self, user_id: int, offset: int = 0) -> dict:
        if type(offset) is not int or offset < 0 or offset > 1000000:
            raise CasinoError("Некорректная страница истории.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM spins WHERE user_id=? ORDER BY created DESC,request_id DESC LIMIT 21 OFFSET ?",
                (user_id, offset),
            ).fetchall()
        return {"items": [self._history_item(row) for row in rows[:20]],
                "next_offset": offset + 20 if len(rows) > 20 else None}

    def spin(self, user_id: int, bet: int, request_id: str) -> dict:
        if type(bet) is not int or bet not in BETS:
            raise CasinoError("Выберите ставку 10, 25, 50 или 100 очков.")
        try:
            if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
                raise ValueError("Invalid request ID")
        except (ValueError, AttributeError):
            raise CasinoError("Некорректный идентификатор вращения.") from None
        clock = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM spins WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["user_id"] != user_id or previous["bet"] != bet:
                    raise CasinoError("Запрос уже использован с другой ставкой.", 409)
                return self._history_item(previous)
            wallet = self._wallet(db, user_id)
            if clock - wallet["last_spin"] < 2.5:
                raise CasinoError("Дождитесь остановки барабанов.", 429)
            if wallet["balance"] < bet:
                raise CasinoError("Не хватает виртуальных очков.", 409)
            reels = [secrets.randbelow(len(SYMBOLS)) for _ in range(3)]
            factor = multiplier(reels)
            payout = bet * factor
            balance = wallet["balance"] - bet + payout
            result = {
                "id": request_id, "reels": reels, "bet": bet, "multiplier": factor,
                "payout": payout, "net": payout - bet, "balance": balance,
                "spins": wallet["spins"] + 1, "won": wallet["won"] + payout,
                "mode": "fruit_slots", "created": clock,
            }
            db.execute(
                "UPDATE wallets SET balance=?,spins=spins+1,won=won+?,last_spin=? WHERE user_id=?",
                (balance, payout, clock, user_id),
            )
            db.execute("INSERT INTO spins(request_id,user_id,bet,result,created,mode) VALUES (?,?,?,?,?,?)",
                       (request_id, user_id, bet, json.dumps(result), clock, "fruit_slots"))
        return result

    def refill(self, user_id: int) -> dict:
        clock = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            wallet = self._wallet(db, user_id)
            if wallet["balance"] >= min(BETS):
                raise CasinoError("Бонус доступен, когда осталось меньше 10 очков.", 409)
            if wallet["last_refill"] and clock - wallet["last_refill"] < 86400:
                raise CasinoError("Следующий бесплатный бонус будет доступен через 24 часа.", 429)
            db.execute("UPDATE wallets SET balance=balance+?,last_refill=? WHERE user_id=?", (START_BALANCE, clock, user_id))
        return self.state(user_id)
