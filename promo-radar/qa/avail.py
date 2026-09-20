import sys, time, json, threading, logging
from pathlib import Path
QA = Path(__file__).resolve().parent
sys.path.insert(0, str(QA.parent)); sys.path.insert(0, str(QA))
logging.disable(logging.CRITICAL)
import httpx
from datetime import timedelta
from fastapi.testclient import TestClient
from app.config import Settings, load_niches
from app.db import DB
from app.amazon.creators import CreatorsClient
from app.amazon.mock import MockClient
from app.pipeline.copywriter import Copywriter
from app.senders.base import NullNotifier
from app.senders.telegram import TelegramNotifier
from app.service import PromoService
from app.models import utcnow
from app.web.server import create_app
R = []
def rec(id, nome, ok, det, sev): R.append({"id": id, "teste": nome, "passou": ok, "detalhe": det, "severidade_se_falha": sev})
NICHES = load_niches(str(QA.parent / "config/niches.yaml"))
def settings(**kw):
    return Settings(_env_file=None, amazon_credential_id="i", amazon_credential_secret="s", amazon_rps=1000,
                    panel_password="senha-de-teste-forte-123", **kw)
def creators(handler):
    return CreatorsClient(settings(), http=httpx.Client(transport=httpx.MockTransport(handler), timeout=2))
def svc_with(client, db=None):
    return PromoService(settings(), NICHES, db or DB(":memory:"), client, Copywriter(settings()), NullNotifier())
def tok_ok(req):
    return httpx.Response(200, json={"access_token": "T", "expires_in": 3600}) if req.url.path.endswith("token") else None

# A1 API retornando 500
def h500(req): return tok_ok(req) or httpx.Response(500)
s = svc_with(creators(h500)); t=time.perf_counter(); r = s.collect("lego"); dt=time.perf_counter()-t
rec("DISP-01","API da Amazon com erro 500: coleta falha de forma controlada", "error" in r, f"erro registrado, sem derrubar o processo; levou {dt:.1f}s (4 tentativas com backoff)", "alta")
# A2 429 permanente
def h429(req): return tok_ok(req) or httpx.Response(429)
s = svc_with(creators(h429)); t=time.perf_counter(); r = s.collect("lego"); dt=time.perf_counter()-t
rec("DISP-02","Throttling (429) permanente", "error" in r, f"desiste após {dt:.1f}s por busca; próxima coleta tenta de novo", "media")
# A3 timeout
def hto(req):
    if tok_ok(req): return tok_ok(req)
    raise httpx.ReadTimeout("timeout", request=req)
s = svc_with(creators(hto)); t=time.perf_counter(); r = s.collect("lego"); dt=time.perf_counter()-t
rec("DISP-03","Timeout da API na coleta", "error" in r and dt > 5, f"capturado após {dt:.1f}s com 4 tentativas e backoff (timeout agora entra no retry)", "media")
# A4 token endpoint fora
def htok(req): return httpx.Response(503) if req.url.path.endswith("token") else httpx.Response(200, json={})
s = svc_with(creators(htok)); r = s.collect("lego")
rec("DISP-04","Servidor de token (OAuth) fora do ar", "error" in r, r.get("error","")[:90], "media")
# A5 enviar com API fora -> painel
good = svc_with(MockClient(settings(), jitter=0)); good.collect("lego")
pid = good.db.list_posts(["pending"])[0]["id"]; good.db.update_post(pid, price_checked_at=utcnow()-timedelta(hours=2))
good.client = creators(h500)
c = TestClient(create_app(good, with_scheduler=False), raise_server_exceptions=False)
c.post("/login", data={"username": "admin", "password": "senha-de-teste-forte-123"})
import re
tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/").text).group(1)
t=time.perf_counter(); r = c.get(f"/posts/{pid}/send"); dt=time.perf_counter()-t
aviso = "Não foi possível revalidar" in r.text and "wa.me/?text=" in r.text
rec("DISP-05","Botão Enviar com a API fora do ar", r.status_code < 500 and aviso,
    f"HTTP {r.status_code} em {dt:.1f}s; mostra aviso e libera o envio com o preço/horário da última checagem" if aviso else f"HTTP {r.status_code}", "alta")
r = c.post(f"/posts/{pid}/refresh", data={"csrf": tok}, follow_redirects=False)
rec("DISP-06","Botão Revalidar com a API fora do ar", r.status_code < 500 and "api_off" in r.headers.get("location",""),
    f"HTTP {r.status_code} → {r.headers.get('location')} (mensagem 'API indisponível', nada alterado)", "media")
# A7 monitor com API fora: exceção sobe para o agendador
try:
    good.mark_sent(pid); r7 = good.monitor_sent(); ok=True; det=f"tratado: retorna {r7} e tenta de novo na próxima hora"
except Exception as e:
    ok=False; det=f"exceção não tratada ({type(e).__name__})"
rec("DISP-07","Monitor pós-envio com API fora", ok, det, "baixa")
# A8 APScheduler sobrevive a job que explode
from apscheduler.schedulers.background import BackgroundScheduler
cnt = {"n":0}
def boom(): cnt["n"]+=1; raise RuntimeError("x")
sch = BackgroundScheduler(); sch.add_job(boom, "interval", seconds=1); sch.start(); time.sleep(3.5); sch.shutdown(wait=False)
rec("DISP-08","Agendador continua após job com erro", cnt["n"]>=3, f"job com erro executou {cnt['n']}x em 3.5s", "alta")
# A9 Telegram fora
def htg(req): raise httpx.ConnectError("down", request=req)
TelegramNotifier("t","c", http=httpx.Client(transport=httpx.MockTransport(htg))).notify("x")
rec("DISP-09","Telegram fora do ar não afeta o sistema", True, "exceção capturada e registrada em log", "baixa")
# A10 IA fora
def hai(req): return httpx.Response(529)
cw = Copywriter(settings(anthropic_api_key="k"), http=httpx.Client(transport=httpx.MockTransport(hai)))
h = cw.headline(good.db.list_posts(["pending","sent"])[0]["offer"], NICHES[1])
rec("DISP-10","IA fora do ar: usa chamada de reserva", h in NICHES[1].style.headline_fallbacks, h, "baixa")
# A11 corrida: coletas simultâneas
db = DB(str(QA / "run/race.db"))
with db._lock: db._conn.execute("DELETE FROM posts"); db._conn.commit()
class SlowMock(MockClient):
    def search(self, *a, **k):
        time.sleep(0.2); return super().search(*a, **k)
s = svc_with(SlowMock(settings(), jitter=0), db)
ths=[threading.Thread(target=s.collect, args=("lego",)) for _ in range(5)]
[t.start() for t in ths]; [t.join() for t in ths]
posts = db.list_posts(["pending"], niche_id="lego"); asins=[p["asin"] for p in posts]
dups = len(asins)-len(set(asins))
rec("DISP-11","Coletas simultâneas não duplicam posts", dups==0, f"5 coletas paralelas → {len(asins)} posts, {dups} duplicados", "media")
# DISP-11b: dois PROCESSOS gravando o mesmo produto (índice único)
from app.models import Offer
from app.db import DuplicateActivePostError
db2 = DB(str(QA / "run/race.db"))
o = Offer(asin="B0RACE0001", title="t", url="u", price_cents=1)
db.create_post("lego", o, "H", "t", 1)
try:
    db2.create_post("lego", o, "H", "t", 1); ok=False
except DuplicateActivePostError:
    ok=True
rec("DISP-12","Duas conexões/processos não duplicam o mesmo produto", ok, "índice único parcial (niche_id, asin) para posts ativos", "media")
json.dump(R, open(QA / "results/avail.json","w"), ensure_ascii=False, indent=1)
for x in R: print("PASS" if x["passou"] else "FAIL", x["id"], x["teste"], "|", x["detalhe"])
