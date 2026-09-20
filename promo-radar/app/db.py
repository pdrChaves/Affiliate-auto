"""Persistência em SQLite (arquivo único). Troca para Postgres = reescrever só este módulo."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .models import Offer, PostStatus, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_id TEXT NOT NULL,
    asin TEXT NOT NULL,
    status TEXT NOT NULL,
    headline TEXT,
    text TEXT NOT NULL,
    price_cents INTEGER,
    basis_cents INTEGER,
    discount_pct REAL,
    score REAL,
    offer_json TEXT NOT NULL,
    price_checked_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    sent_at TEXT,
    ended_at TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS ix_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS ix_posts_asin ON posts(niche_id, asin);

CREATE TABLE IF NOT EXISTS watchlist (
    niche_id TEXT NOT NULL,
    asin TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (niche_id, asin)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    fetched INTEGER DEFAULT 0,
    queued INTEGER DEFAULT 0,
    rejected_json TEXT,
    error TEXT
);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def offer_to_json(o: Offer) -> str:
    d = asdict(o)
    d["fetched_at"] = o.fetched_at.isoformat()
    return json.dumps(d, ensure_ascii=False)


def offer_from_json(s: str) -> Offer:
    d = json.loads(s)
    d["fetched_at"] = datetime.fromisoformat(d["fetched_at"])
    return Offer(**d)


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ---------- expurgo (Licença: só ASIN pode ficar guardado indefinidamente) ----------
    def purge_product_content(self, older_than: datetime, statuses: list[str]) -> int:
        """Apaga título/preço/imagem/texto de posts antigos, mantendo só ASIN, nicho, status e datas."""
        rows = self._all(f"SELECT id, asin FROM posts WHERE created_at < ? AND text != '[conteúdo expurgado]' "
                         f"AND status IN ({','.join('?' * len(statuses))})", (older_than.isoformat(), *statuses))
        for r in rows:
            blank = Offer(asin=r["asin"], title="", url="", price_cents=None, in_stock=False)
            self._exec("""UPDATE posts SET text='[conteúdo expurgado]', headline=NULL, price_cents=NULL,
                          basis_cents=NULL, offer_json=?, note=COALESCE(note,'') || ' [expurgado]' WHERE id=?""",
                       (offer_to_json(blank), r["id"]))
        return len(rows)

    # ---------- posts ----------
    def create_post(self, niche_id: str, offer: Offer, headline: str, text: str, score: float) -> int:
        cur = self._exec(
            """INSERT INTO posts(niche_id, asin, status, headline, text, price_cents, basis_cents, discount_pct,
               score, offer_json, price_checked_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (niche_id, offer.asin, PostStatus.PENDING.value, headline, text, offer.price_cents, offer.basis_cents,
             offer.discount_pct, score, offer_to_json(offer), offer.fetched_at.isoformat(), utcnow().isoformat()),
        )
        return cur.lastrowid

    def get_post(self, post_id: int) -> dict | None:
        rows = self._all("SELECT * FROM posts WHERE id=?", (post_id,))
        return self._post(rows[0]) if rows else None

    def list_posts(self, statuses: list[str] | None = None, niche_id: str | None = None, limit: int = 200) -> list[dict]:
        sql, params = "SELECT * FROM posts WHERE 1=1", []
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params += statuses
        if niche_id:
            sql += " AND niche_id=?"
            params.append(niche_id)
        sql += " ORDER BY score DESC, created_at DESC LIMIT ?"
        params.append(limit)
        return [self._post(r) for r in self._all(sql, tuple(params))]

    def update_post(self, post_id: int, **fields) -> None:
        if not fields:
            return
        conv = {}
        for k, v in fields.items():
            if isinstance(v, datetime):
                v = v.isoformat()
            elif isinstance(v, PostStatus):
                v = v.value
            elif isinstance(v, Offer):
                k, v = "offer_json", offer_to_json(v)
            conv[k] = v
        cols = ", ".join(f"{k}=?" for k in conv)
        self._exec(f"UPDATE posts SET {cols} WHERE id=?", (*conv.values(), post_id))

    def last_post_for(self, niche_id: str, asin: str, statuses: list[str]) -> dict | None:
        q = f"""SELECT * FROM posts WHERE niche_id=? AND asin=? AND status IN ({','.join('?' * len(statuses))})
                ORDER BY created_at DESC LIMIT 1"""
        rows = self._all(q, (niche_id, asin, *statuses))
        return self._post(rows[0]) if rows else None

    @staticmethod
    def _post(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["offer"] = offer_from_json(d.pop("offer_json"))
        for k in ("price_checked_at", "created_at", "approved_at", "sent_at", "ended_at"):
            d[k] = _dt(d[k])
        return d

    # ---------- watchlist ----------
    def add_watch(self, niche_id: str, asin: str) -> None:
        self._exec("INSERT OR IGNORE INTO watchlist(niche_id, asin, added_at) VALUES (?,?,?)",
                   (niche_id, asin.strip().upper(), utcnow().isoformat()))

    def remove_watch(self, niche_id: str, asin: str) -> None:
        self._exec("DELETE FROM watchlist WHERE niche_id=? AND asin=?", (niche_id, asin))

    def watchlist(self, niche_id: str) -> list[str]:
        return [r["asin"] for r in self._all("SELECT asin FROM watchlist WHERE niche_id=?", (niche_id,))]

    # ---------- runs ----------
    def log_run(self, niche_id: str, fetched: int, queued: int, rejected: dict, error: str | None = None) -> None:
        self._exec("INSERT INTO runs(niche_id, started_at, fetched, queued, rejected_json, error) VALUES (?,?,?,?,?,?)",
                   (niche_id, utcnow().isoformat(), fetched, queued, json.dumps(rejected, ensure_ascii=False), error))

    def recent_runs(self, limit: int = 20) -> list[dict]:
        rows = self._all("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["rejected"] = json.loads(d.pop("rejected_json") or "{}")
            d["started_at"] = _dt(d["started_at"])
            out.append(d)
        return out
