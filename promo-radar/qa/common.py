"""Utilitários compartilhados da bateria de QA."""
import os
import re
from pathlib import Path

import httpx

QA = Path(__file__).resolve().parent
ROOT = QA.parent
RESULTS = QA / "results"
PASSWORD = os.getenv("QA_PASSWORD", "senha-de-teste-forte-123")


def login(client: httpx.Client, password: str = PASSWORD) -> httpx.Response:
    return client.post("/login", data={"username": "admin", "password": password}, follow_redirects=False)


def csrf(client: httpx.Client) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)
