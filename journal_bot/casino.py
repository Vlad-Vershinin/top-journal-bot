from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl
from uuid import UUID


SYMBOLS = ("🍌", "🍒", "🍋", "🍇", "🍉", "⭐")
TRIPLES = (8, 10, 12, 15, 20, 40)
BETS = (10, 25, 50, 100)
START_BALANCE = 1000


class CasinoError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validate_init_data(raw: str, token: str, owner: int | None, now: float | None = None) -> int:
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
    return user_id


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
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _wallet(db: sqlite3.Connection, user_id: int) -> sqlite3.Row:
        db.execute("INSERT OR IGNORE INTO wallets(user_id,balance) VALUES (?,?)", (user_id, START_BALANCE))
        return db.execute("SELECT * FROM wallets WHERE user_id=?", (user_id,)).fetchone()

    def state(self, user_id: int) -> dict:
        with self._connect() as db:
            wallet = self._wallet(db, user_id)
            history = db.execute(
                "SELECT result FROM spins WHERE user_id=? ORDER BY created DESC LIMIT 10", (user_id,)
            ).fetchall()
        return {
            "balance": wallet["balance"], "spins": wallet["spins"], "won": wallet["won"],
            "refill_at": wallet["last_refill"] + 86400 if wallet["last_refill"] else 0,
            "symbols": SYMBOLS, "triples": TRIPLES, "bets": BETS,
            "history": [json.loads(row["result"]) for row in history],
        }

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
                return json.loads(previous["result"])
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
            }
            db.execute(
                "UPDATE wallets SET balance=?,spins=spins+1,won=won+?,last_spin=? WHERE user_id=?",
                (balance, payout, clock, user_id),
            )
            db.execute("INSERT INTO spins VALUES (?,?,?,?,?)", (request_id, user_id, bet, json.dumps(result), clock))
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
