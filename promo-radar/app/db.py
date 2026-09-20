"""Persistência em SQLite (arquivo único). Troca para Postgres = reescrever só este módulo.

- Modo WAL: leituras do painel não ficam bloqueadas enquanto uma escrita (ex.: expurgo) acontece.
- Índice único parcial: impede o mesmo produto duas vezes na fila do mesmo nicho, mesmo com coletas
  simultâneas ou em processos diferentes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Offer, PostStatus, utcnow

PURGED_TEXT = "[conteúdo expurgado]"
ACTIVE = (PostStatus.PENDING.value, PostStatus.APPROVED.value)

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
    note TEXT,
    purged INTEGER NOT NULL DEFAULT 0
);
DROP INDEX IF EXISTS ix_posts_status;
CREATE INDEX IF NOT EXISTS ix_posts_rank ON posts(status, score, created_at);
CREATE INDEX IF NOT EXISTS ix_posts_asin ON posts(niche_id, asin);
CREATE INDEX IF NOT EXISTS ix_posts_created ON posts(purged, created_at);

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
CREATE INDEX IF NOT EXISTS ix_runs_started ON runs(started_at);
"""

UNIQUE_ACTIVE = """CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_active ON posts(niche_id, asin)
                   WHERE status IN ('pending', 'approved')"""

# Colunas que update_post aceita (evita montar SQL com nomes arbitrários)
UPDATABLE = {"status", "headline", "text", "price_cents", "basis_cents", "discount_pct", "score", "offer_json",
             "price_checked_at", "created_at", "approved_at", "sent_at", "ended_at", "note"}
OFFER_FIELDS = {f.name for f in fields(Offer)}


class DuplicateActivePostError(Exception):
    """O produto já está na fila deste nicho."""


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def offer_to_json(o: Offer) -> str:
    d = asdict(o)
    d["fetched_at"] = o.fetched_at.isoformat()
    return json.dumps(d, ensure_ascii=False)


def offer_from_json(s: str) -> Offer:
    d = {k: v for k, v in json.loads(s).items() if k in OFFER_FIELDS}
    d.setdefault("title", "")
    d.setdefault("url", "")
    d.setdefault("price_cents", None)
    d["fetched_at"] = datetime.fromisoformat(d["fetched_at"]) if d.get("fetched_at") else utcnow()
    return Offer(**d)


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(posts)")}
        if cols and "purged" not in cols:   # banco da v1
            self._conn.execute("ALTER TABLE posts ADD COLUMN purged INTEGER NOT NULL DEFAULT 0")
            self._conn.execute("UPDATE posts SET purged=1 WHERE text=?", (PURGED_TEXT,))
        self._conn.executescript(SCHEMA)
        # duplicatas antigas (bug da v1) impediriam o índice único: mantém a mais recente
        self._conn.execute("""UPDATE posts SET status='expired', note='duplicata removida na migração'
            WHERE status IN ('pending', 'approved') AND id NOT IN (
              SELECT MAX(id) FROM posts WHERE status IN ('pending', 'approved') GROUP BY niche_id, asin)""")
        self._conn.execute(UNIQUE_ACTIVE)

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ---------- saúde ----------
    def check(self) -> None:
        """Levanta exceção se o banco não puder ser lido E escrito (ou se o arquivo sumiu do disco)."""
        if self.path != ":memory:" and not Path(self.path).is_file():
            raise RuntimeError(f"arquivo do banco não encontrado: {self.path}")
        with self._lock:
            self._conn.execute("SELECT 1 FROM posts LIMIT 1").fetchall()
            self._conn.execute("BEGIN IMMEDIATE")   # pega o lock de escrita (falha se read-only/travado)
            self._conn.execute("ROLLBACK")

    # ---------- expurgo e retenção ----------
    def purge_product_content(self, older_than: datetime, statuses: list[str]) -> int:
        """Apaga título/preço/imagem/texto de posts antigos (um único UPDATE), mantendo ASIN, nicho, status e datas.
        Licença do Creators API: só o ASIN pode ser guardado indefinidamente."""
        cur = self._exec(
            """UPDATE posts SET text=?, headline=NULL, price_cents=NULL, basis_cents=NULL, purged=1,
                  offer_json=json_object('asin', asin, 'title', '', 'url', '', 'price_cents', NULL,
                                         'in_stock', 0, 'fetched_at', created_at),
                  note=COALESCE(note, '') || ' [expurgado]'
                WHERE purged=0 AND created_at < ? AND status IN (SELECT value FROM json_each(?))""",
            (PURGED_TEXT, older_than.isoformat(), json.dumps(statuses)))
        return cur.rowcount

    def delete_old(self, posts_before: datetime, runs_before: datetime) -> tuple[int, int]:
        """Remove de vez posts já expurgados e o log de coletas antigo."""
        p = self._exec("DELETE FROM posts WHERE purged=1 AND created_at < ?", (posts_before.isoformat(),)).rowcount
        r = self._exec("DELETE FROM runs WHERE started_at < ?", (runs_before.isoformat(),)).rowcount
        return p, r

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")

    # ---------- posts ----------
    def create_post(self, niche_id: str, offer: Offer, headline: str, text: str, score: float) -> int:
        try:
            cur = self._exec(
                """INSERT INTO posts(niche_id, asin, status, headline, text, price_cents, basis_cents, discount_pct,
                   score, offer_json, price_checked_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (niche_id, offer.asin, PostStatus.PENDING.value, headline, text, offer.price_cents,
                 offer.basis_cents, offer.discount_pct, score, offer_to_json(offer), offer.fetched_at.isoformat(),
                 utcnow().isoformat()),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicateActivePostError(offer.asin) from e
        return int(cur.lastrowid or 0)

    def get_post(self, post_id: int) -> dict | None:
        rows = self._all("SELECT * FROM posts WHERE id=?", (post_id,))
        return self._post(rows[0]) if rows else None

    @staticmethod
    def _filter(statuses: list[str] | None, niche_id: str | None) -> tuple[str, list[Any]]:
        """Monta o WHERE só com placeholders "?" (a quantidade varia; nenhum valor é interpolado)."""
        st = statuses or [s.value for s in PostStatus]
        where = f" WHERE status IN ({','.join('?' * len(st))})"  # nosec B608
        params: list[Any] = list(st)
        if niche_id:
            where += " AND niche_id = ?"
            params.append(niche_id)
        return where, params

    def list_posts(self, statuses: list[str] | None = None, niche_id: str | None = None, limit: int = 200,
                   offset: int = 0) -> list[dict]:
        where, params = self._filter(statuses, niche_id)
        # 2 etapas: ordena só os ids pelo índice (status, score, created_at) e depois busca as linhas da página.
        # Evita carregar/ordenar milhares de linhas completas quando a fila ou o histórico crescem.
        ids_sql = "SELECT id FROM posts" + where + " ORDER BY score DESC, created_at DESC LIMIT ? OFFSET ?"  # nosec B608
        ids = [r["id"] for r in self._all(ids_sql, (*params, limit, offset))]
        if not ids:
            return []
        rows = self._all(f"SELECT * FROM posts WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))  # nosec B608
        by_id = {r["id"]: r for r in rows}
        return [self._post(by_id[i]) for i in ids]

    def count_posts(self, statuses: list[str] | None = None, niche_id: str | None = None) -> int:
        where, params = self._filter(statuses, niche_id)
        return int(self._all("SELECT COUNT(*) AS n FROM posts" + where, tuple(params))[0]["n"])  # nosec B608

    def update_post(self, post_id: int, **changes: Any) -> None:
        conv: dict[str, Any] = {}
        for k, v in changes.items():
            if isinstance(v, datetime):
                v = v.isoformat()
            elif isinstance(v, PostStatus):
                v = v.value
            elif isinstance(v, Offer):
                k, v = "offer_json", offer_to_json(v)
            if k not in UPDATABLE:
                raise ValueError(f"coluna não permitida: {k}")
            conv[k] = v
        if conv:
            cols = ", ".join(f"{k}=?" for k in conv)
            # colunas validadas contra a whitelist UPDATABLE acima; valores sempre como parâmetros
            self._exec(f"UPDATE posts SET {cols} WHERE id=?", (*conv.values(), post_id))  # nosec B608

    def last_post_for(self, niche_id: str, asin: str, statuses: list[str]) -> dict | None:
        rows = self._all("""SELECT * FROM posts WHERE niche_id=? AND asin=?
                            AND status IN (SELECT value FROM json_each(?))
                            ORDER BY created_at DESC LIMIT 1""", (niche_id, asin, json.dumps(statuses)))
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
