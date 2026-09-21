import sys, time, json, os, random
from pathlib import Path
OUT = Path(__file__).resolve().parent / "results"
sys.path.insert(0, str(OUT.parent.parent))
from datetime import timedelta
from app.db import DB
from app.models import Offer, PostStatus, utcnow
path = sys.argv[1]; N = int(sys.argv[2])
if os.path.exists(path): os.remove(path)
db = DB(path); rnd = random.Random(1)
statuses = [PostStatus.SENT]*6 + [PostStatus.EXPIRED]*2 + [PostStatus.REJECTED, PostStatus.PENDING]
t = time.perf_counter()
with db._lock:
    rows=[]
    for i in range(N):
        o = Offer(asin=f"B0VOL{i:05d}", title="Produto de volume "*4, url="https://www.amazon.com.br/dp/x?tag=t-20",
                  price_cents=rnd.randint(1000, 90000), basis_cents=100000)
        from app.db import offer_to_json
        st = statuses[i % 10] if i < N-200 else PostStatus.PENDING
        created = (utcnow() - timedelta(minutes=(N - i))).isoformat()
        cat = "VideoGames" if i%2 else "Electronics"
        rows.append((o.asin, st.value, "H", "texto "*60, o.price_cents, o.basis_cents, 30, rnd.random()*50,
                     offer_to_json(o), created, created, created if st==PostStatus.SENT else None,
                     cat, "teclado" if i%3 else "fone de ouvido"))
    db._conn.executemany("""INSERT INTO posts(asin, status, headline, text, price_cents, basis_cents, discount_pct,
      score, offer_json, price_checked_at, created_at, sent_at, category, query)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    db._conn.commit()
ins = time.perf_counter()-t
def tm(fn, n=20):
    t=time.perf_counter()
    for _ in range(n): fn()
    return round((time.perf_counter()-t)/n*1000, 2)
res = {"posts": N, "insercao_s": round(ins,2), "tamanho_db_MB": round(os.path.getsize(path)/1e6,1),
 "list_posts_fila_ms": tm(lambda: db.list_posts(["pending","approved"])),
 "list_posts_enviados_ms": tm(lambda: db.list_posts(["sent"])),
 "last_post_for_ms": tm(lambda: db.last_post_for("B0VOL00123", ["sent","pending"]), 200),
 "get_post_ms": tm(lambda: db.get_post(N//2), 200),
 "filtro_categoria_ms": tm(lambda: db.list_posts(["pending","approved"], category="Electronics")),
 "filtro_texto_ms": tm(lambda: db.list_posts(["pending","approved"], termo="teclado")),
 "contagem_por_categoria_ms": tm(lambda: db.count_by_category(["pending","approved"]))}
if len(sys.argv)>3: print(json.dumps(res)); sys.exit()
t=time.perf_counter(); n=db.purge_product_content(utcnow()-timedelta(hours=24), ["sent","expired","rejected"]); res["purge_s"]=round(time.perf_counter()-t,2); res["purge_linhas"]=n
res["tamanho_db_pos_purge_MB"]=round(os.path.getsize(path)/1e6,1)
print(json.dumps(res, ensure_ascii=False)); json.dump(res, open(OUT / f"volume_{N}.json", "w"), indent=1)
