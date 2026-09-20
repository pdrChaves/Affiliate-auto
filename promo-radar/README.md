# Promo Radar

Coleta promoções da Amazon (Creators API), valida o desconto, monta o post no formato das comunidades de WhatsApp e deixa pronto para enviar com 1 clique.

- Documentação completa: [`RELATORIO.md`](RELATORIO.md)
- Fluxograma (o que é automático e o que é manual): [`FLUXOGRAMA.md`](FLUXOGRAMA.md)
- Avaliação de qualidade/segurança: [`RELATORIO_QA_v2.md`](RELATORIO_QA_v2.md)

## Rodar em 1 minuto (modo demonstração, sem credenciais)

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.lock -r requirements-dev.txt
cp .env.example .env                                  # CATALOG_MODE=mock
# edite o .env e defina PANEL_PASSWORD (mínimo 12 caracteres). Sem isso o painel não sobe.
python -m app.cli serve                               # http://127.0.0.1:8000
```

Entre com `PANEL_USER` / `PANEL_PASSWORD` e clique em **Coletar agora**.

## Com a Amazon de verdade

No `.env`: `CATALOG_MODE=creators`, `AMAZON_PARTNER_TAG`, `AMAZON_CREDENTIAL_ID`, `AMAZON_CREDENTIAL_SECRET`, `AMAZON_CREDENTIAL_VERSION`.
As credenciais saem do Associates Central → Ferramentas → Creators API. Para ter acesso é preciso ter **10 vendas qualificadas nos últimos 30 dias**.

## Docker

```bash
cp .env.example .env    # defina PANEL_PASSWORD
docker compose up -d --build
```

O painel fica em `http://127.0.0.1:8000`, acessível só desta máquina. Para abrir em outro aparelho, use VPN (ex.: Tailscale) ou um proxy com HTTPS e `COOKIE_SECURE=true`.

## Testes

```bash
pytest -q              # 66 testes
ruff check app tests && mypy app
```
