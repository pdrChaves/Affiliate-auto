"""Linha de comando.

  python -m app.cli serve            # painel + agendador (uso normal)
  python -m app.cli collect [nicho]  # uma coleta agora e sai
  python -m app.cli monitor          # checa promoções já enviadas
  python -m app.cli purge            # expurga conteúdo antigo (regra das 24h)
  python -m app.cli preview          # imprime os posts da fila no terminal
"""
from __future__ import annotations

import json
import os
import sys


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "serve"
    if cmd == "serve":
        import uvicorn
        uvicorn.run("app.web.server:app_from_env", factory=True, host=os.getenv("HOST", "0.0.0.0"),
                    port=int(os.getenv("PORT", "8000")))
        return
    from .app_factory import build_service
    svc = build_service()
    if cmd == "collect":
        res = svc.collect(argv[1]) if len(argv) > 1 else svc.collect_all()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd == "monitor":
        print("encerrados:", svc.monitor_sent())
    elif cmd == "purge":
        print("expirados:", svc.expire_stale_queue(), "| expurgados:", svc.purge_old_content())
    elif cmd == "preview":
        for p in svc.db.list_posts(["pending", "approved"]):
            print(f"\n===== #{p['id']} [{p['niche_id']}] nota {p['score']} {p['note'] or ''}\n{p['text']}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
