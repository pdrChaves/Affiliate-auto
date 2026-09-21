"""Linha de comando.

  python -m app.cli serve            # painel + agendador (uso normal)
  python -m app.cli buscar "teclado" [Categoria]   # pesquisa e enfileira o que passa nas regras
  python -m app.cli salvas           # roda todas as buscas salvas + a watchlist
  python -m app.cli monitor          # checa promoções já enviadas
  python -m app.cli purge            # expurga conteúdo antigo (regra das 24h)
  python -m app.cli preview          # imprime os posts da fila no terminal
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "serve"
    if cmd == "serve":
        import uvicorn

        from .config import get_settings
        s = get_settings()
        uvicorn.run("app.web.server:app_from_env", factory=True, host=s.host, port=s.port,
                    proxy_headers=False, server_header=False)
        return
    if cmd == "categorias":
        from .categories import as_table
        from .config import get_settings
        mk = get_settings().amazon_marketplace
        print(f"Departamentos de {mk} (os mesmos do menu do site):\n{as_table(mk)}\n"
              "Use na barra de pesquisa do painel ou no terminal, por exemplo:\n"
              '  python -m app.cli buscar "teclado mecânico" "Computadores e Informática"')
        return
    from .app_factory import build_service
    svc = build_service()
    if cmd == "buscar":
        if len(argv) < 2:
            print('uso: python -m app.cli buscar "teclado" [Categoria]')
            sys.exit(1)
        res = svc.run_search(argv[1], argv[2] if len(argv) > 2 else None)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd == "salvas":
        print(json.dumps(svc.run_saved_searches(), ensure_ascii=False, indent=2))
    elif cmd == "monitor":
        print("encerrados:", svc.monitor_sent())
    elif cmd == "purge":
        print("expirados:", svc.expire_stale_queue(), "| expurgados:", svc.purge_old_content(),
              "| limpeza:", svc.housekeeping())
    elif cmd == "preview":
        for p in svc.db.list_posts(["pending", "approved"]):
            print(f"\n===== #{p['id']} [{p['query'] or '-'}] nota {p['score']} {p['note'] or ''}\n{p['text']}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
