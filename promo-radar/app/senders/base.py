"""Camada de ENVIO/NOTIFICAÇÃO — isolada de propósito.

v1: o envio para o WhatsApp é feito por você (1 clique no painel: wa.me → escolhe a comunidade → enviar).
Motivo: os Termos do WhatsApp proíbem auto-messaging e clientes não autorizados, e a API oficial
(Cloud API / Groups API) só permite grupos de até 8 participantes, sem Comunidades.

Se no futuro surgir um canal oficial que atenda, basta implementar `Notifier`/`Publisher` aqui.
"""
from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def notify(self, text: str) -> None: ...


class NullNotifier:
    def notify(self, text: str) -> None:
        pass
