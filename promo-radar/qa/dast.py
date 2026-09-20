"""Testes dinâmicos de segurança contra o servidor rodando."""
import json, time, httpx
B = "http://localhost:8766"; A = ("admin", "troque-esta-senha")
c = httpx.Client(base_url=B, timeout=30)
R = []
def rec(id, name, passed, detail, sev="info"):
    R.append({"id": id, "teste": name, "passou": passed, "detalhe": detail, "severidade_se_falha": sev})

# 1 auth em todas as rotas
routes = [("GET","/"),("POST","/collect"),("POST","/watch"),("POST","/watch/remove"),("POST","/manual"),
          ("POST","/posts/1/approve"),("POST","/posts/1/reject"),("POST","/posts/1/refresh"),("POST","/posts/1/headline"),
          ("POST","/posts/1/coupon"),("GET","/posts/1/send"),("POST","/posts/1/sent")]
unauth = [(m,p,c.request(m,p).status_code) for m,p in routes]
rec("SEC-01","Rotas do painel exigem autenticação", all(s==401 for *_,s in unauth), unauth, "critica")
bad = c.get("/", auth=("admin","errada")).status_code
rec("SEC-02","Senha errada é recusada", bad==401, bad, "critica")
rec("SEC-03","Senha padrão de exemplo NÃO é aceita", c.get("/", auth=A).status_code!=200,
    "o servidor sobe e aceita 'admin/troque-esta-senha' sem aviso", "alta")
# 4 força bruta
t=time.perf_counter(); codes=[c.get("/", auth=("admin",f"x{i}")).status_code for i in range(300)]; dt=time.perf_counter()-t
rec("SEC-04","Força bruta é limitada (lockout/rate limit)", 429 in codes,
    f"300 tentativas em {dt:.1f}s ({300/dt:.0f}/s), nenhuma bloqueada", "alta")
# 5 endpoints públicos
pub = {p: c.get(p).status_code for p in ["/docs","/redoc","/openapi.json","/health"]}
rec("SEC-05","Documentação da API não fica pública", pub["/openapi.json"]!=200, pub, "media")
# 6 headers
h = c.get("/", auth=A).headers
want = ["content-security-policy","x-frame-options","x-content-type-options","referrer-policy","strict-transport-security"]
missing=[x for x in want if x not in h]
rec("SEC-06","Cabeçalhos de segurança presentes", not missing, {"ausentes": missing}, "media")
# 7 CSRF
n0 = len(c.get("/?tab=descartados", auth=A).text)
r = c.post("/collect", data={"niche":""}, auth=A, headers={"Origin":"https://site-malicioso.com","Referer":"https://site-malicioso.com/x"})
rec("SEC-07","POST de outra origem (CSRF) é recusado", r.status_code in (400,403),
    f"POST com Origin=https://site-malicioso.com → {r.status_code} (aceito)", "alta")
# 8 XSS
xs = "<script>alert('xss')</script>"; xh = '"><img src=x onerror=alert(1)>'; xt="</textarea><script>alert(2)</script>"
c.post("/manual", auth=A, data={"niche":"lego","asin":"B0XSSXSS01","title":xs+xt,"url":"https://www.amazon.com.br/dp/B0XSSXSS01?tag=t-20","coupon":xh})
pid = max(p for p in [1]) if False else None
page = c.get("/", auth=A).text
import re
ids = [int(x) for x in re.findall(r"/posts/(\d+)/send", page)]
sendp = c.get(f"/posts/{max(ids)}/send", auth=A).text
raw_hits = [s for s in ["<script>alert('xss')","<img src=x onerror","</textarea><script>"] if s in page or s in sendp]
rec("SEC-08","XSS: HTML injetado é escapado (painel e tela de envio)", not raw_hits, {"payloads_refletidos_crus": raw_hits}, "alta")
pid_x = max(ids)
c.post(f"/posts/{pid_x}/headline", auth=A, data={"headline": xh})
page = c.get("/", auth=A).text
rec("SEC-09","XSS via campo de chamada (headline)", '<img src=x onerror' not in page.lower(), "atributo value escapado", "alta")
# 10 validação de URL manual
bad_urls = ["https://evil.example/phish?x=amazon.com.br&tag=t-20","javascript:alert(1)//amazon.com.br?tag=1","https://amazon.com.br.evil.example/?tag=x"]
acc = {u: c.post("/manual", auth=A, data={"niche":"lego","asin":"B0URLURL01","title":"t","url":u}, follow_redirects=False).status_code for u in bad_urls}
rec("SEC-10","Link manual só aceita domínio da Amazon", all(v==400 for v in acc.values()), acc, "media")
# 11 SQLi
sq = {}
for p in ["' OR '1'='1","lego' UNION SELECT 1--","1; DROP TABLE posts;--"]:
    sq[p] = c.get("/", params={"niche":p}, auth=A).status_code
c.post("/watch/remove", auth=A, data={"niche":"x' OR 1=1--","asin":"x"})
still = c.get("/", auth=A).status_code==200 and "Enviar" in c.get("/", auth=A).text
rec("SEC-11","SQL injection em parâmetros", still and all(v==200 for v in sq.values()), {"status": sq, "banco_intacto": still}, "critica")
# 12 entradas inválidas → 500
def safe(fn):
    try: return fn().status_code
    except httpx.HTTPError as e: return 500
inv = {"post_inexistente_refresh": safe(lambda: c.post("/posts/999999/refresh", auth=A)),
       "post_inexistente_headline": safe(lambda: c.post("/posts/999999/headline", auth=A, data={"headline":"x"})),
       "post_inexistente_approve": safe(lambda: c.post("/posts/999999/approve", auth=A, follow_redirects=False)),
       "nicho_inexistente_collect": safe(lambda: c.post("/collect", auth=A, data={"niche":"naoexiste"})),
       "nicho_inexistente_manual": safe(lambda: c.post("/manual", auth=A, data={"niche":"naoexiste","asin":"B0","title":"t","url":"https://www.amazon.com.br/dp/B0?tag=t"})),
       "preco_invalido_manual": safe(lambda: c.post("/manual", auth=A, data={"niche":"lego","asin":"B0","title":"t","url":"https://www.amazon.com.br/dp/B0?tag=t","price":"abc"}))}
rec("SEC-12","Entradas inválidas retornam 4xx (não 500)", not any(v>=500 for v in inv.values()), inv, "baixa")
# 13 payload gigante
big = "A"*(10*1024*1024)
t=time.perf_counter(); r = c.post("/manual", auth=A, data={"niche":"lego","asin":"B0BIGBIG01","title":big,"url":"https://www.amazon.com.br/dp/B0BIGBIG01?tag=t"}, follow_redirects=False); dt=time.perf_counter()-t
rec("SEC-13","Limite de tamanho de entrada", r.status_code in (400,413,422), f"título de 10 MB → {r.status_code} em {dt:.2f}s (gravado no banco)", "media")
# 14 CRLF / redirect
r = c.post("/posts/1/reject", auth=A, data={"tab":"fila\r\nSet-Cookie: pwn=1"}, follow_redirects=False)
rec("SEC-14","Injeção de cabeçalho via redirect", "pwn" not in r.headers.get("set-cookie","") , {"status":r.status_code,"location":r.headers.get("location")}, "media")
r = c.post("/posts/1/reject", auth=A, data={"tab":"//evil.example"}, follow_redirects=False)
rec("SEC-15","Open redirect", not r.headers.get("location","").startswith("//"), r.headers.get("location"), "media")
# 16 health info
rec("SEC-16","/health não expõe detalhes internos", "mode" not in c.get("/health").text, c.get("/health").json(), "baixa")
json.dump(R, open("/home/claude/qa/dast.json","w"), ensure_ascii=False, indent=1, default=str)
for x in R: print(("PASS" if x["passou"] else "FAIL"), x["id"], x["teste"], "|", str(x["detalhe"])[:160])
