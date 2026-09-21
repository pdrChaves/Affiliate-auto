"""Persistência em SQLite (arquivo único). Troca para Postgres = reescrever só este módulo.

- Modo WAL: leituras do painel não ficam bloqueadas enquanto uma escrita (ex.: expurgo) acontece.
- Índice único parcial: impede o mesmo produto duas vezes na fila, mesmo com buscas
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
    purged INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    query TEXT
);
DROP INDEX IF EXISTS ix_posts_status;
CREATE INDEX IF NOT EXISTS ix_posts_rank ON posts(status, score, created_at);
CREATE INDEX IF NOT EXISTS ix_posts_asin ON posts(asin);
CREATE INDEX IF NOT EXISTS ix_posts_created ON posts(purged, created_at);
DROP INDEX IF EXISTS ix_posts_category;
-- (categoria, status, score, created_at): o filtro por categoria e a contagem dos chips
-- saem do índice já ordenados, sem B-tree temporária, mesmo com o banco grande.
CREATE INDEX IF NOT EXISTS ix_posts_cat_rank ON posts(category, status, score, created_at);

CREATE TABLE IF NOT EXISTS watchlist (
    asin TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keywords TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'All',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    last_queued INTEGER DEFAULT 0,
    UNIQUE (keywords, category)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    started_at TEXT NOT NULL,
    fetched INTEGER DEFAULT 0,
    queued INTEGER DEFAULT 0,
    rejected_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_started ON runs(started_at);
"""

UNIQUE_ACTIVE = """CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_active ON posts(asin)
                   WHERE status IN ('pending', 'approved')"""

# Teto de linhas varridas pelas contagens do painel. Sem ele, o filtro de texto (LIKE) percorre
# a tabela inteira só para escrever "N posts" no rodapé; com ele a conta para em 5.000 e a tela
# mostra "5000+". Não afeta a listagem em si, que já é paginada e sai do índice.
COUNT_CAP = 5000

# Categorias gravadas pelas versões que usavam o searchIndex da API → departamento do site.
SEARCH_INDEX_ANTIGO = {
    "All": "", "Books": "Livros", "Computers": "Computadores e Informática",
    "Electronics": "Eletrônicos, TV e Áudio", "HomeAndKitchen": "Casa, Jardim e Limpeza",
    "KindleStore": "Livros", "MobileApps": "Games e Consoles",
    "OfficeProducts": "Papelaria e Escritório", "ToolsAndHomeImprovement": "Ferramentas e Construção",
    "VideoGames": "Games e Consoles",
}

# Colunas que update_post aceita (evita montar SQL com nomes arbitrários)
_MARKS_ACTIVE = "'pending', 'approved'"

UPDATABLE = {"status", "headline", "text", "price_cents", "basis_cents", "discount_pct", "score", "offer_json",
             "price_checked_at", "created_at", "approved_at", "sent_at", "ended_at", "note", "category", "query"}
OFFER_FIELDS = {f.name for f in fields(Offer)}


class DuplicateActivePostError(Exception):
    """O produto já está na fila."""


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
        if cols and "purged" not in cols:    # banco da v1
            self._conn.execute("ALTER TABLE posts ADD COLUMN purged INTEGER NOT NULL DEFAULT 0")
            self._conn.execute("UPDATE posts SET purged=1 WHERE text=?", (PURGED_TEXT,))
        if cols and "category" not in cols:  # anterior à filtragem por categoria
            self._conn.execute("ALTER TABLE posts ADD COLUMN category TEXT")
        if cols and "query" not in cols:
            self._conn.execute("ALTER TABLE posts ADD COLUMN query TEXT")
        if "niche_id" in cols:               # versões com nicho: a coluna deixa de existir
            self._conn.execute("UPDATE posts SET query = COALESCE(query, niche_id)")
            # O SQLite recusa DROP COLUMN enquanto qualquer índice citar a coluna (os da v1 eram
            # (niche_id, asin)). Derruba todos os índices de posts: o SCHEMA logo abaixo recria os atuais.
            for (nome,) in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='posts' "
                    "AND name NOT LIKE 'sqlite_autoindex%'").fetchall():
                self._conn.execute(f'DROP INDEX IF EXISTS "{nome}"')  # nosec B608 - nome vindo do próprio banco
            self._conn.execute("ALTER TABLE posts DROP COLUMN niche_id")
        wcols = {r[1] for r in self._conn.execute("PRAGMA table_info(watchlist)")}
        if "niche_id" in wcols:              # watchlist passa a ser única, sem nicho
            self._conn.execute("""CREATE TABLE IF NOT EXISTS watchlist_novo (
                asin TEXT PRIMARY KEY, added_at TEXT NOT NULL)""")
            self._conn.execute("INSERT OR IGNORE INTO watchlist_novo SELECT asin, added_at FROM watchlist")
            self._conn.execute("DROP TABLE watchlist")
            self._conn.execute("ALTER TABLE watchlist_novo RENAME TO watchlist")
        rcols = {r[1] for r in self._conn.execute("PRAGMA table_info(runs)")}
        if "niche_id" in rcols:
            self._conn.execute("ALTER TABLE runs RENAME COLUMN niche_id TO query")
        self._conn.executescript(SCHEMA)
        # v1.3: a categoria era o searchIndex da API ("Electronics", "All"...). Agora é o
        # departamento do site ("Eletrônicos, TV e Áudio"). Traduz o que já estava gravado.
        for antigo, novo in SEARCH_INDEX_ANTIGO.items():
            self._conn.execute("UPDATE posts SET category=? WHERE category=?", (novo, antigo))
            self._conn.execute("UPDATE searches SET category=? WHERE category=?", (novo, antigo))
        # duplicatas antigas impediriam o índice único: mantém a mais recente de cada ASIN
        self._conn.execute("""UPDATE posts SET status='expired', note='duplicata removida na migração'
            WHERE status IN ('pending', 'approved') AND id NOT IN (
              SELECT MAX(id) FROM posts WHERE status IN ('pending', 'approved') GROUP BY asin)""")
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
        """Apaga título/preço/imagem/texto de posts antigos (um único UPDATE), mantendo ASIN, categoria, status e datas.
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
    def create_post(self, offer: Offer, headline: str, text: str, score: float, query: str = "") -> int:
        try:
            cur = self._exec(
                """INSERT INTO posts(asin, status, headline, text, price_cents, basis_cents, discount_pct,
                   score, offer_json, price_checked_at, created_at, category, query)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (offer.asin, PostStatus.PENDING.value, headline, text, offer.price_cents,
                 offer.basis_cents, offer.discount_pct, score, offer_to_json(offer), offer.fetched_at.isoformat(),
                 utcnow().isoformat(), offer.category, query),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicateActivePostError(offer.asin) from e
        return int(cur.lastrowid or 0)

    def get_post(self, post_id: int) -> dict | None:
        rows = self._all("SELECT * FROM posts WHERE id=?", (post_id,))
        return self._post(rows[0]) if rows else None

    @staticmethod
    def _filter(statuses: list[str] | None, category: str | None = None,
                termo: str | None = None) -> tuple[str, list[Any]]:
        """Monta o WHERE só com placeholders "?" (a quantidade varia; nenhum valor é interpolado)."""
        st = statuses or [s.value for s in PostStatus]
        where = f" WHERE status IN ({','.join('?' * len(st))})"  # nosec B608
        params: list[Any] = list(st)
        if category:
            where += " AND category = ?"
            params.append(category)
        if termo:
            where += " AND (LOWER(text) LIKE ? OR LOWER(COALESCE(query, '')) LIKE ?)"
            like = f"%{termo.lower()}%"
            params += [like, like]
        return where, params

    def list_posts(self, statuses: list[str] | None = None, limit: int = 200, offset: int = 0,
                   category: str | None = None, termo: str | None = None) -> list[dict]:
        where, params = self._filter(statuses, category, termo)
        # 2 etapas: ordena só os ids pelo índice (status, score, created_at) e depois busca as linhas da página.
        # Evita carregar/ordenar milhares de linhas completas quando a fila ou o histórico crescem.
        ids_sql = "SELECT id FROM posts" + where + " ORDER BY score DESC, created_at DESC LIMIT ? OFFSET ?"  # nosec B608
        ids = [r["id"] for r in self._all(ids_sql, (*params, limit, offset))]
        if not ids:
            return []
        rows = self._all(f"SELECT * FROM posts WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))  # nosec B608
        by_id = {r["id"]: r for r in rows}
        return [self._post(by_id[i]) for i in ids]

    def count_posts(self, statuses: list[str] | None = None, category: str | None = None,
                    termo: str | None = None) -> int:
        where, params = self._filter(statuses, category, termo)
        sql = f"SELECT COUNT(*) AS n FROM (SELECT 1 FROM posts{where} LIMIT {COUNT_CAP})"  # nosec B608
        return int(self._all(sql, tuple(params))[0]["n"])

    def count_by_category(self, statuses: list[str] | None = None, termo: str | None = None) -> dict[str, int]:
        """Quantos posts por categoria da Amazon (para os filtros do painel)."""
        where, params = self._filter(statuses, None, termo)
        # Sem filtro de texto o ix_posts_cat_rank cobre a consulta inteira (agrupa lendo o índice,
        # sem B-tree temporária). Com termo, o LIKE precisa da coluna text, então deixa o SQLite escolher.
        if termo:   # com LIKE não há índice possível: varre no máximo COUNT_CAP linhas
            sql = (f"SELECT c, COUNT(*) AS n FROM (SELECT COALESCE(category, '') AS c FROM posts"  # nosec B608
                   f"{where} LIMIT {COUNT_CAP}) GROUP BY c")
        else:       # o ix_posts_cat_rank cobre a consulta: agrupa lendo o índice, sem B-tree temporária
            sql = ("SELECT COALESCE(category, '') AS c, COUNT(*) AS n "  # nosec B608
                   "FROM posts INDEXED BY ix_posts_cat_rank" + where + " GROUP BY category")
        return {r["c"]: r["n"] for r in self._all(sql, tuple(params))}

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

    def last_post_for(self, asin: str, statuses: list[str]) -> dict | None:
        rows = self._all("""SELECT * FROM posts WHERE asin=?
                            AND status IN (SELECT value FROM json_each(?))
                            ORDER BY created_at DESC LIMIT 1""", (asin, json.dumps(statuses)))
        return self._post(rows[0]) if rows else None

    @staticmethod
    def _post(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["offer"] = offer_from_json(d.pop("offer_json"))
        for k in ("price_checked_at", "created_at", "approved_at", "sent_at", "ended_at"):
            d[k] = _dt(d[k])
        return d

    # ---------- watchlist ----------
    def add_watch(self, asin: str) -> None:
        self._exec("INSERT OR IGNORE INTO watchlist(asin, added_at) VALUES (?,?)",
                   (asin.strip().upper(), utcnow().isoformat()))

    def remove_watch(self, asin: str) -> None:
        self._exec("DELETE FROM watchlist WHERE asin=?", (asin,))

    def watchlist(self) -> list[str]:
        return [r["asin"] for r in self._all("SELECT asin FROM watchlist ORDER BY added_at DESC")]

    # ---------- buscas salvas ----------
    def save_search(self, keywords: str, category: str) -> int:
        cur = self._exec("""INSERT INTO searches(keywords, category, created_at) VALUES (?,?,?)
                            ON CONFLICT(keywords, category) DO UPDATE SET enabled=1""",
                         (keywords, category, utcnow().isoformat()))
        return int(cur.lastrowid or 0)

    def searches(self, only_enabled: bool = False) -> list[dict]:
        # sem interpolação de dados: só um filtro fixo é concatenado
        sql = "SELECT * FROM searches" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY keywords"  # nosec B608
        out = []
        for r in self._all(sql):
            d = dict(r)
            d["last_run_at"] = _dt(d["last_run_at"])
            d["enabled"] = bool(d["enabled"])
            out.append(d)
        return out

    def toggle_search(self, search_id: int, enabled: bool) -> None:
        self._exec("UPDATE searches SET enabled=? WHERE id=?", (int(enabled), search_id))

    def delete_search(self, search_id: int) -> None:
        self._exec("DELETE FROM searches WHERE id=?", (search_id,))

    def mark_search_run(self, search_id: int, queued: int) -> None:
        self._exec("UPDATE searches SET last_run_at=?, last_queued=? WHERE id=?",
                   (utcnow().isoformat(), queued, search_id))

    # ---------- runs ----------
    def log_run(self, query: str, fetched: int, queued: int, rejected: dict, error: str | None = None) -> None:
        self._exec("INSERT INTO runs(query, started_at, fetched, queued, rejected_json, error) VALUES (?,?,?,?,?,?)",
                   (query, utcnow().isoformat(), fetched, queued, json.dumps(rejected, ensure_ascii=False), error))

    def recent_runs(self, limit: int = 20) -> list[dict]:
        rows = self._all("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["rejected"] = json.loads(d.pop("rejected_json") or "{}")
            d["started_at"] = _dt(d["started_at"])
            out.append(d)
        return out
