from fastapi.testclient import TestClient

from app.web.server import create_app

AUTH = ("admin", "troque-esta-senha")


def test_panel_flow(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    assert c.get("/").status_code == 401
    assert c.post("/collect", data={"niche": ""}, auth=AUTH, follow_redirects=False).status_code == 303
    page = c.get("/", auth=AUTH)
    assert page.status_code == 200 and "Enviar" in page.text and "<s>De R$" in page.text
    pid = svc.db.list_posts(["pending"])[0]["id"]
    c.post(f"/posts/{pid}/headline", data={"headline": "nova chamada"}, auth=AUTH)
    assert "*NOVA CHAMADA*" in svc.db.get_post(pid)["text"]
    c.post(f"/posts/{pid}/coupon", data={"coupon": "LEGO10"}, auth=AUTH)
    send = c.get(f"/posts/{pid}/send", auth=AUTH)
    assert "wa.me/?text=" in send.text and "LEGO10" in send.text
    c.post(f"/posts/{pid}/sent", auth=AUTH)
    assert svc.db.get_post(pid)["status"] == "sent"
    assert "monitorado" in c.get("/?tab=enviados", auth=AUTH).text


def test_watch_validation(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    assert c.post("/watch", data={"niche": "lego", "asin": "abc"}, auth=AUTH).status_code == 400
    assert c.post("/manual", data={"niche": "lego", "asin": "B0X", "title": "t", "url": "https://bit.ly/x"},
                  auth=AUTH).status_code == 400
