"""Avisos para VOCÊ (operador) via bot oficial do Telegram: 'post novo na fila', 'promo encerrada'.
Não posta em comunidade nenhuma; é só um alerta no seu celular."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, http: httpx.Client | None = None):
        self.token, self.chat_id = token, chat_id
        self.http = http or httpx.Client(timeout=15)

    def notify(self, text: str) -> None:
        try:
            self.http.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                           json={"chat_id": self.chat_id, "text": text[:4000],
                                 "disable_web_page_preview": True})
        except Exception as e:
            log.warning("Telegram falhou: %s", e)
