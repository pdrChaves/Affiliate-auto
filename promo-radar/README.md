# Promo Radar

Coleta promoções da Amazon (Creators API), valida o desconto, monta o post no formato das comunidades de WhatsApp e deixa pronto para enviar com 1 clique.

**Documentação completa:** [`RELATORIO.md`](RELATORIO.md)

## Rodar em 1 minuto (modo demonstração, sem credenciais)

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                  # CATALOG_MODE=mock
python -m app.cli serve                               # http://localhost:8000  (admin / troque-esta-senha)
```

No painel, clique em **Coletar agora**.

## Com a Amazon de verdade

No `.env`: `CATALOG_MODE=creators`, `AMAZON_PARTNER_TAG`, `AMAZON_CREDENTIAL_ID`, `AMAZON_CREDENTIAL_SECRET`, `AMAZON_CREDENTIAL_VERSION`.
As credenciais saem do Associates Central → Ferramentas → Creators API. Para ter acesso é preciso ter **10 vendas qualificadas nos últimos 30 dias**.

## Docker

```bash
cp .env.example .env && docker compose up -d --build
```

## Testes

```bash
pytest -q
```
