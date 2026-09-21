import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.web.server import create_app

from .conftest import PASSWORD


def login(c, password=PASSWORD):
    return c.post("/login", data={"username": "admin", "password": password}, follow_redirects=False)


def csrf_of(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/").text).group(1)


@pytest.fixture
def client(svc):
    c = TestClient(create_app(svc, with_scheduler=False), base_url="http://painel.local")
    assert login(c).status_code == 303
    return c


# ---------- acesso ----------
def test_requires_login(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.post("/buscas/rodar").status_code == 401
    assert c.get("/buscar?q=teclado", follow_redirects=False).status_code == 303


def test_refuses_weak_or_missing_password(svc):
    for pw in ["", "troque-esta-senha", "curta", "admin", "1234567", "senha123"]:
        svc.s.panel_password = pw
        with pytest.raises(SystemExit):
            create_app(svc, with_scheduler=False)


def test_session_cookie_flags(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    cookie = login(c).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_bruteforce_lockout(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    codes = [login(c, "errada-errada-1").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert login(c).status_code == 429


def test_csrf_token_and_origin(client):
    assert client.post("/buscas/rodar", data={}).status_code == 403
    assert client.post("/buscas/rodar", data={"csrf": "x"}).status_code == 403
    tok = csrf_of(client)
    r = client.post("/buscas/rodar", data={"csrf": tok}, headers={"Origin": "https://mal.example"})
    assert r.status_code == 403
    assert client.post("/buscas/rodar", data={"csrf": tok}, follow_redirects=False).status_code == 303


def test_origin_null_is_accepted_with_csrf(client):
    tok = csrf_of(client)
    r = client.post("/buscas/rodar", data={"csrf": tok}, headers={"Origin": "null"}, follow_redirects=False)
    assert r.status_code == 303


def test_security_headers_and_no_docs(client):
    h = client.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "same-origin"
    for p in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(p).status_code == 404


# ---------- barra de pesquisa ----------
def test_search_page_lists_results_with_verdict(client):
    page = client.get("/buscar?q=bluetooth&cat=Electronics").text
    assert "2 resultado" in page or "resultado(s)" in page
    assert "passa nas regras" in page and "desconto_baixo" in page      # aprovados e reprovados
    assert "Colocar na fila" in page and "Salvar esta busca" in page
    assert "#publi" in page                                             # já mostra o post que sairia


def test_search_page_validates_input(client):
    assert "2 letras" in client.get("/buscar?q=a").text
    assert client.get("/buscar?q=teclado&cat=Fashion").text.count("Todos os departamentos") >= 1  # cai no All
    vazia = client.get("/buscar").text
    assert "resultado" not in vazia                                      # sem termo, sem busca


def test_queue_from_search_results(client, svc):
    tok = csrf_of(client)
    r = client.post("/buscar/fila", data={"csrf": tok, "asin": "B0MOCK0101", "q": "headset gamer"},
                    follow_redirects=False)
    assert r.status_code == 303 and "ok=B0MOCK0101" in r.headers["location"]
    p = svc.db.list_posts(["pending"])[0]
    assert p["asin"] == "B0MOCK0101" and p["query"] == "headset gamer"
    fila = client.get("/").text
    assert 'busca &#34;headset gamer&#34;' in fila or "headset gamer" in fila


def test_save_search_and_run(client, svc):
    tok = csrf_of(client)
    client.post("/buscar/salvar", data={"csrf": tok, "q": "bluetooth", "cat": "Eletrônicos, TV e Áudio"})
    assert [(b["keywords"], b["category"]) for b in svc.db.searches()] == [("bluetooth", "Eletrônicos, TV e Áudio")]
    assert "bluetooth" in client.get("/").text                            # aparece em "Buscas salvas"
    client.post("/buscas/rodar", data={"csrf": tok})
    assert svc.db.count_posts(["pending"]) == 2
    sid = svc.db.searches()[0]["id"]
    client.post(f"/buscas/{sid}/toggle", data={"csrf": tok, "enabled": "0"})
    assert svc.db.searches()[0]["enabled"] is False
    client.post(f"/buscas/{sid}/remover", data={"csrf": tok})
    assert svc.db.searches() == []


# ---------- fila ----------
def test_category_filter_uses_the_site_departments(client, svc):
    """Os chips são os 19 departamentos do menu do amazon.com.br, e a categoria vem do PRODUTO."""
    tok = csrf_of(client)
    for asin in ["B0MOCK0001", "B0MOCK0101", "B0MOCK0104", "B0MOCK0301"]:
        client.post("/buscar/fila", data={"csrf": tok, "asin": asin, "q": "teste"})
    page = client.get("/").text
    for nome in ["Pet Shop", "Roupas, Calçados e Acessórios", "Brinquedos e Jogos", "Livros",
                 "Bebês", "Automotivo", "Alimentos e Bebidas", "Filmes, Séries e Música"]:
        assert nome in page, nome                       # os 19 do site, não os 10 da API
    assert "Eletrônicos, TV e Áudio (1)" in page and "Livros (1)" in page
    assert client.get("/?cat=Eletrônicos, TV e Áudio").text.count('class="card"') == 1
    assert client.get("/?cat=Livros").text.count('class="card"') == 1
    assert client.get("/?cat=Pet Shop").text.count('class="card"') == 0
    assert client.get("/?cat=INVENTADA").text.count('class="card"') == 4     # inválida é ignorada


def test_text_filter_over_the_queue(client, svc):
    tok = csrf_of(client)
    client.post("/buscar/fila", data={"csrf": tok, "asin": "B0MOCK0104", "q": "teclado mecânico",
                                      "cat": "Computadores e Informática"})
    client.post("/buscar/fila", data={"csrf": tok, "asin": "B0MOCK0001", "q": "fone"})
    assert client.get("/?q=teclado").text.count('class="card"') == 1
    assert client.get("/?q=fone").text.count('class="card"') == 1
    assert client.get("/?q=mochila").text.count('class="card"') == 0


def test_actions_return_json_and_keep_filters(client, svc):
    tok = csrf_of(client)
    client.post("/buscar/fila", data={"csrf": tok, "asin": "B0MOCK0101", "q": "headset"})
    pid = svc.db.list_posts(["pending"])[0]["id"]
    fetch = {"X-Requested-With": "fetch"}
    r = client.post(f"/posts/{pid}/headline", data={"csrf": tok, "headline": "chamada nova"}, headers=fetch)
    body = r.json()
    assert r.status_code == 200 and body["headline"] == "CHAMADA NOVA" and not body["removed"]
    assert "CHAMADA NOVA" in body["text_html"]
    r = client.post(f"/posts/{pid}/coupon", data={"csrf": tok, "coupon": "CUPOM10"}, headers=fetch)
    assert "CUPOM10" in r.json()["text_html"]
    r = client.post(f"/posts/{pid}/reject", data={"csrf": tok}, headers=fetch)
    assert r.json()["removed"] and r.json()["status"] == "rejected"
    r = client.post("/posts/9999/coupon", data={"csrf": tok, "coupon": "x"}, headers=fetch)
    assert r.status_code == 404 and "erro" in r.json()


def test_actions_without_js_return_to_same_filters(client, svc):
    tok = csrf_of(client)
    client.post("/buscar/fila", data={"csrf": tok, "asin": "B0MOCK0101", "q": "headset"})
    pid = svc.db.list_posts(["pending"])[0]["id"]
    r = client.post(f"/posts/{pid}/approve",
                    data={"csrf": tok, "tab": "fila", "cat": "Games e Consoles", "q": "headset", "page": "1"},
                    follow_redirects=False)
    destino = r.headers["location"]
    assert r.status_code == 303
    assert "cat=Games+e+Consoles" in destino and "q=headset" in destino and "tab=fila" in destino


def test_panel_flow(client, svc):
    tok = csrf_of(client)
    client.post("/buscar/fila", data={"csrf": tok, "asin": "B0MOCK0101", "q": "headset"})
    pid = svc.db.list_posts(["pending"])[0]["id"]
    send = client.get(f"/posts/{pid}/send")
    assert "wa.me/?text=" in send.text and "<script>" not in send.text
    client.post(f"/posts/{pid}/sent", data={"csrf": tok})
    assert svc.db.get_post(pid)["status"] == "sent"
    assert "Enviado em" in client.get("/?tab=enviados").text
    assert client.post(f"/posts/{pid}/reject", data={"csrf": tok}).status_code == 409


def test_watch_and_manual(client, svc):
    tok = csrf_of(client)
    client.post("/watch", data={"csrf": tok, "asin": "B0MOCK0104"})
    assert svc.db.watchlist() == ["B0MOCK0104"]
    client.post("/watch/remove", data={"csrf": tok, "asin": "B0MOCK0104"})
    assert svc.db.watchlist() == []
    client.post("/manual", data={"csrf": tok, "asin": "b0manual01", "title": "Teclado X",
                                 "url": "https://evil.example/phish"})
    p = svc.db.list_posts(["pending"])[0]
    assert p["offer"].url == "https://www.amazon.com.br/dp/B0MANUAL01?tag=teste-20"
    assert "evil" not in p["text"]


def test_invalid_inputs_are_4xx(client):
    tok = csrf_of(client)
    assert client.post("/posts/999/refresh", data={"csrf": tok}).status_code == 404
    assert client.get("/posts/999/send").status_code == 404
    assert client.post("/buscar/fila", data={"csrf": tok, "asin": "abc"}).status_code == 400
    assert client.post("/watch", data={"csrf": tok, "asin": "abc"}).status_code == 400
    assert client.post("/manual", data={"csrf": tok, "asin": "B0XXXXXXX1", "title": "t",
                                        "price": "abc"}).status_code == 400
    assert client.post("/buscar/salvar", data={"csrf": tok, "q": "a"}).status_code == 400


def test_body_limit_and_field_limits(client):
    tok = csrf_of(client)
    assert client.post("/manual", data={"csrf": tok, "asin": "B0BIGBIG01",
                                        "title": "A" * 70000}).status_code == 413
    assert client.post("/manual", data={"csrf": tok, "asin": "B0BIGBIG01",
                                        "title": "A" * 400}).status_code == 422


def test_pagination(client, svc):
    from app.models import Offer
    for i in range(65):
        svc.db.create_post(Offer(asin=f"B0PAGE{i:04d}", title="t", url="u", price_cents=1), "H", "t", i)
    assert "página 1 de 3" in client.get("/").text
    assert "página 3 de 3" in client.get("/?page=99").text


def test_logout(client):
    tok = csrf_of(client)
    client.post("/logout", data={"csrf": tok})
    assert client.get("/", follow_redirects=False).status_code == 303


def test_health(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    assert c.get("/health").json() == {"ok": True}

    def broken():
        raise RuntimeError("disco cheio")
    svc.db.check = broken
    r = c.get("/health")
    assert r.status_code == 503 and r.json() == {"ok": False}


def test_health_reports_dead_scheduler(svc):
    app = create_app(svc, with_scheduler=False)
    c = TestClient(app)

    class Dead:
        running = False
    app.state.scheduler = Dead()
    assert c.get("/health").status_code == 503


def test_settings_password_rules():
    base = dict(_env_file=None, panel_user="admin")
    assert Settings(**base, panel_password="").password_problem()
    assert Settings(**base, panel_password="troque-esta-senha").password_problem()
    assert Settings(**base, panel_password="1234567").password_problem()
    assert Settings(**base, panel_password="12345678").password_problem()
    assert Settings(**base, panel_password="chaves-2026").password_problem() is None
