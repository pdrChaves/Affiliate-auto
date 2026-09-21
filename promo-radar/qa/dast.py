"""Testes dinâmicos de segurança contra o servidor rodando (v2: login por sessão)."""
import json
import subprocess
import sys
import time

import httpx

from common import PASSWORD, RESULTS, ROOT, csrf, login

B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8766"
R = []


def rec(id, name, passed, detail, sev="info"):
    R.append({"id": id, "teste": name, "passou": passed, "detalhe": detail, "severidade_se_falha": sev})


def safe(fn):
    try:
        return fn().status_code
    except httpx.HTTPError:
        return 599


anon = httpx.Client(base_url=B, timeout=30)
# 1 autenticação em todas as rotas
routes = [("GET", "/"), ("GET", "/buscar"), ("POST", "/buscar/fila"), ("POST", "/buscar/salvar"),
          ("POST", "/buscas/rodar"), ("POST", "/watch"), ("POST", "/watch/remove"), ("POST", "/manual"),
          ("POST", "/posts/1/approve"), ("POST", "/posts/1/reject"), ("POST", "/posts/1/refresh"),
          ("POST", "/posts/1/headline"), ("POST", "/posts/1/coupon"), ("GET", "/posts/1/send"), ("POST", "/posts/1/sent")]
res = [(m, p, anon.request(m, p, follow_redirects=False)) for m, p in routes]
ok = all((r.status_code == 303 and r.headers.get("location") == "/login") if m == "GET" else r.status_code in (401, 403)
         for m, p, r in res)
rec("SEC-01", "Rotas do painel exigem autenticação", ok, [(m, p, r.status_code) for m, p, r in res], "critica")
rec("SEC-02", "Senha errada é recusada", login(anon, "senha-errada-123").status_code == 401, "401", "critica")

# 3 senha padrão: o servidor precisa se recusar a subir
proc = subprocess.run([sys.executable, "-c", "from app.web.server import create_app; create_app(with_scheduler=False)"],
                      cwd=ROOT, capture_output=True, text=True, timeout=60,
                      env={"PATH": "/usr/bin:/bin", "PANEL_PASSWORD": "troque-esta-senha", "CATALOG_MODE": "mock",
                           "DATABASE_PATH": ":memory:"})
rec("SEC-03", "Senha padrão/fraca impede o painel de subir", proc.returncode != 0 and "Painel não iniciado" in proc.stderr,
    proc.stderr.strip().splitlines()[-1][:160] if proc.stderr else proc.returncode, "alta")

pub = {p: anon.get(p).status_code for p in ["/docs", "/redoc", "/openapi.json"]}
rec("SEC-05", "Documentação da API não fica pública", all(v == 404 for v in pub.values()), pub, "media")

c = httpx.Client(base_url=B, timeout=30)
lr = login(c)
cookie = lr.headers.get("set-cookie", "")
logged = lr.status_code == 303
h = c.get("/").headers
want = ["content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy"]
missing = [x for x in want if x not in h]
rec("SEC-06", "Cabeçalhos de segurança presentes", logged and not missing,
    {"ausentes": missing, "csp": h.get("content-security-policy", "")[:80] + "…",
     "hsts": "ligado só com COOKIE_SECURE=true (HTTPS)"}, "media")

tok = csrf(c)
a = c.post("/buscas/rodar", data={}, follow_redirects=False).status_code
b = c.post("/buscas/rodar", data={"csrf": "forjado"}, follow_redirects=False).status_code
o = c.post("/buscas/rodar", data={"csrf": tok}, headers={"Origin": "https://site-malicioso.com"},
           follow_redirects=False).status_code
legit = c.post("/buscas/rodar", data={"csrf": tok}, follow_redirects=False).status_code
rec("SEC-07", "CSRF: POST sem token, com token forjado ou de outra origem é recusado",
    a == 403 and b == 403 and o == 403 and legit == 303 and "samesite=strict" in cookie.lower(),
    {"sem_token": a, "token_forjado": b, "origin_malicioso": o, "legitimo": legit,
     "cookie": "HttpOnly; SameSite=Strict" if "samesite=strict" in cookie.lower() else cookie}, "alta")

xs = "<script>alert('xss')</script>"
xh = '"><img src=x onerror=alert(1)>'
xt = "</textarea><script>alert(2)</script>"
c.post("/manual", data={"csrf": tok, "asin": "B0XSSXSS01", "title": (xs + xt)[:290], "coupon": xh[:30]})
page = c.get("/").text
import re  # noqa: E402

ids = [int(x) for x in re.findall(r"/posts/(\d+)/send", page)]
sendp = c.get(f"/posts/{max(ids)}/send").text
raw = [s for s in ["<script>alert('xss')", "<img src=x onerror", "</textarea><script>"] if s in page or s in sendp]
rec("SEC-08", "XSS: HTML injetado é escapado (painel e tela de envio)", not raw, {"refletidos_crus": raw}, "alta")
c.post(f"/posts/{max(ids)}/headline", data={"csrf": tok, "headline": xh})
rec("SEC-09", "XSS via campo de chamada (headline)", "<img src=x onerror" not in c.get("/").text.lower(), "escapado", "alta")

bad_urls = ["https://evil.example/phish?x=amazon.com.br&tag=t-20", "javascript:alert(1)//amazon.com.br?tag=1",
            "https://amazon.com.br.evil.example/?tag=x"]
leaks = []
for i, u in enumerate(bad_urls):
    c.post("/manual", data={"csrf": tok, "asin": f"B0URLURL0{i}", "title": "t", "url": u})
page = c.get("/").text
leaks = [u for u in ["evil.example", "javascript:"] if u in page]
rec("SEC-10", "Post manual não aceita link de terceiros", not leaks,
    "campo de URL removido: o link é sempre amazon.com.br/dp/ASIN?tag=SUA_TAG; URLs enviadas foram ignoradas"
    if not leaks else leaks, "alta")

sq = {p: c.get("/", params={"q": p}).status_code for p in ["' OR '1'='1", "lego' UNION SELECT 1--", "1; DROP TABLE posts;--"]}
c.post("/watch/remove", data={"csrf": tok, "asin": "x' OR 1=1--"})
intact = "Enviar" in c.get("/").text
rec("SEC-11", "SQL injection em parâmetros", intact and all(v == 200 for v in sq.values()), {"status": sq, "banco_intacto": intact}, "critica")

inv = {"post_inexistente_refresh": safe(lambda: c.post("/posts/999999/refresh", data={"csrf": tok})),
       "post_inexistente_headline": safe(lambda: c.post("/posts/999999/headline", data={"csrf": tok, "headline": "x"})),
       "post_inexistente_approve": safe(lambda: c.post("/posts/999999/approve", data={"csrf": tok})),
       "asin_invalido_fila": safe(lambda: c.post("/buscar/fila", data={"csrf": tok, "asin": "abc"})),
       "termo_curto_salvar": safe(lambda: c.post("/buscar/salvar", data={"csrf": tok, "q": "a"})),
       "preco_invalido_manual": safe(lambda: c.post("/manual", data={"csrf": tok, "asin": "B0ABCDEF12", "title": "t", "price": "abc"}))}
err_page = c.post("/posts/999999/refresh", data={"csrf": tok}).text
rec("SEC-12", "Entradas inválidas retornam 4xx (não 500) e sem stack trace",
    all(400 <= v < 500 for v in inv.values()) and "Traceback" not in err_page, inv, "baixa")

big = "A" * (10 * 1024 * 1024)
r = safe(lambda: c.post("/manual", data={"csrf": tok, "asin": "B0BIGBIG01", "title": big}))
r2 = safe(lambda: c.post("/manual", data={"csrf": tok, "asin": "B0BIGBIG01", "title": "A" * 400}))
rec("SEC-13", "Limite de tamanho de entrada", r == 413 and r2 == 422, {"corpo_10MB": r, "titulo_400_chars": r2}, "media")

r = c.post("/posts/1/reject", data={"csrf": tok, "tab": "fila\r\nSet-Cookie: pwn=1"}, follow_redirects=False)
rec("SEC-14", "Injeção de cabeçalho via redirect", "pwn" not in r.headers.get("set-cookie", ""),
    {"status": r.status_code, "location": r.headers.get("location")}, "media")
r = c.post("/posts/2/reject", data={"csrf": tok, "tab": "//evil.example"}, follow_redirects=False)
rec("SEC-15", "Open redirect", not (r.headers.get("location") or "").startswith("//"), r.headers.get("location"), "media")
hj = anon.get("/health").json()
rec("SEC-16", "/health não expõe detalhes internos", set(hj) == {"ok"}, hj, "baixa")

# novos
tok2 = csrf(c)
c.post("/logout", data={"csrf": tok2})
after = c.get("/", follow_redirects=False).status_code
rec("SEC-17", "Logout invalida a sessão no servidor", after == 303, f"GET / após logout → {after} (login)", "media")
forged = httpx.Client(base_url=B, cookies={"pr_session": "a" * 43}).get("/", follow_redirects=False).status_code
rec("SEC-18", "Cookie de sessão forjado é recusado", forged == 303, forged, "alta")

# por último: bloqueia o IP do teste
# 4 força bruta (IP próprio do teste; outro cliente depois)
brute = httpx.Client(base_url=B, timeout=30)
t = time.perf_counter()
codes = [login(brute, f"x-errada-{i:04d}").status_code for i in range(300)]
dt = time.perf_counter() - t
first_block = codes.index(429) + 1 if 429 in codes else None
rec("SEC-04", "Força bruta é limitada", first_block is not None and codes.count(401) <= 5,
    f"300 tentativas em {dt:.1f}s: {codes.count(401)} avaliadas, {codes.count(429)} bloqueadas (429) a partir da "
    f"{first_block}ª; senha correta durante o bloqueio → {login(brute).status_code}", "alta")

R.sort(key=lambda x: int(x['id'].split('-')[1]))
json.dump(R, open(RESULTS / "dast.json", "w"), ensure_ascii=False, indent=1, default=str)
for x in R:
    print(("PASS" if x["passou"] else "FAIL"), x["id"], x["teste"], "|", str(x["detalhe"])[:170])
print(f"\n{sum(x['passou'] for x in R)}/{len(R)} aprovados")
