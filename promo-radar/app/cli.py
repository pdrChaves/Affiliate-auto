"""Linha de comando.

  python -m app.cli serve            # painel + agendador (uso normal)
  python -m app.cli collect [nicho]  # uma coleta agora e sai
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
        print(f"Categorias (search_index) válidas em {mk}:\n{as_table(mk)}\n"
              "Use em config/niches.yaml, por exemplo:\n"
              "  searches:\n"
              "    - { search_index: Electronics, keywords: \"fone de ouvido bluetooth\" }\n"
              "Subcategoria exata: copie o número de 'node=' na URL da categoria no site e use\n"
              "  - { search_index: Electronics, browse_node_id: \"16364755011\" }")
        return
    from .app_factory import build_service
    svc = build_service()
    if cmd == "collect":
        res = svc.collect(argv[1]) if len(argv) > 1 else svc.collect_all()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd == "monitor":
        print("encerrados:", svc.monitor_sent())
    elif cmd == "purge":
        print("expirados:", svc.expire_stale_queue(), "| expurgados:", svc.purge_old_content(),
              "| limpeza:", svc.housekeeping())
    elif cmd == "preview":
        for p in svc.db.list_posts(["pending", "approved"]):
            print(f"\n===== #{p['id']} [{p['niche_id']}] nota {p['score']} {p['note'] or ''}\n{p['text']}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
