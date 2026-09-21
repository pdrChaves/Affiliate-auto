import sqlite3
from datetime import timedelta

from app.db import DB, PURGED_TEXT
from app.models import Offer, utcnow

V1_SCHEMA = """CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, niche_id TEXT NOT NULL, asin TEXT NOT NULL,
 status TEXT NOT NULL, headline TEXT, text TEXT NOT NULL, price_cents INTEGER, basis_cents INTEGER, discount_pct REAL,
 score REAL, offer_json TEXT NOT NULL, price_checked_at TEXT NOT NULL, created_at TEXT NOT NULL, approved_at TEXT,
 sent_at TEXT, ended_at TEXT, note TEXT);"""


def test_migrates_v1_database_with_duplicates(tmp_path):
    path = str(tmp_path / "v1.db")
    c = sqlite3.connect(path)
    c.executescript(V1_SCHEMA)
    now = utcnow().isoformat()
    oj = f'{{"asin":"B0X","title":"t","url":"u","price_cents":1,"fetched_at":"{now}"}}'
    for _ in range(3):   # duplicatas que a v1 permitia
        c.execute("INSERT INTO posts(niche_id,asin,status,text,offer_json,price_checked_at,created_at) "
                  "VALUES ('games','B0X','pending','t',?,?,?)", (oj, now, now))
    c.execute("INSERT INTO posts(niche_id,asin,status,text,offer_json,price_checked_at,created_at) "
              "VALUES ('games','B0Y','sent',?,?,?,?)", (PURGED_TEXT, oj, now, now))
    c.commit()
    c.close()
    db = DB(path)
    assert db.count_posts(["pending"]) == 1 and db.count_posts(["expired"]) == 2
    assert db._all("SELECT purged FROM posts WHERE asin='B0Y'")[0]["purged"] == 1
    assert db._all("PRAGMA journal_mode")[0][0] == "wal"


def test_batch_purge_keeps_only_asin():
    db = DB(":memory:")
    ids = [db.create_post(Offer(asin=f"B0PURGE{i:03d}", title="Produto", url="u", price_cents=100),
                          "H", "texto", 1) for i in range(50)]
    for i in ids:
        db.update_post(i, status="expired", created_at=utcnow() - timedelta(hours=30))
    assert db.purge_product_content(utcnow() - timedelta(hours=24), ["expired"]) == 50
    p = db.get_post(ids[0])
    assert p["text"] == PURGED_TEXT and p["offer"].title == "" and p["asin"] == "B0PURGE000"
    assert db.purge_product_content(utcnow() - timedelta(hours=24), ["expired"]) == 0   # idempotente


def test_update_post_rejects_unknown_columns():
    import pytest
    db = DB(":memory:")
    with pytest.raises(ValueError):
        db.update_post(1, **{"status=1; DROP TABLE posts; --": "x"})


def test_check_and_vacuum(tmp_path):
    db = DB(str(tmp_path / "x.db"))
    db.check()
    db.vacuum()


def test_check_detects_missing_file(tmp_path):
    import pytest
    path = tmp_path / "y.db"
    db = DB(str(path))
    db.check()
    path.unlink()
    with pytest.raises(RuntimeError):
        db.check()


def test_counts_are_capped_so_the_panel_stays_fast(monkeypatch):
    """As contagens do rodapé/chips param no teto: o filtro de texto não varre a tabela inteira."""
    from app import db as dbmod
    monkeypatch.setattr(dbmod, "COUNT_CAP", 10)
    db = DB(":memory:")
    for i in range(25):
        db.create_post(Offer(asin=f"B0CAP{i:05d}", title="t", url="u", price_cents=100),
                       "H", "teclado mecânico", 1, query="teclado")
    assert db.count_posts(["pending"]) == 10                       # 25 posts, conta para em 10
    assert sum(db.count_by_category(["pending"], termo="teclado").values()) == 10
    assert len(db.list_posts(["pending"], limit=50)) == 25          # a listagem em si não é afetada


def test_category_filter_and_counts():
    db = DB(":memory:")
    for i, cat in enumerate(["Electronics", "Electronics", "VideoGames"]):
        db.create_post(Offer(asin=f"B0CAT{i:05d}", title="t", url="u", price_cents=100),
                       "H", "texto", 1, query="busca")
        db.update_post(db.list_posts(["pending"])[0]["id"], category=cat)
    assert db.count_by_category(["pending"]) == {"Electronics": 2, "VideoGames": 1}
    assert db.count_posts(["pending"], category="Electronics") == 2
    assert db.count_by_category(["pending"], termo="inexistente") == {}
