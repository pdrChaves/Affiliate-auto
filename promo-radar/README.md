# Promo Radar

Coleta promoções da Amazon (Creators API), valida o desconto, monta o post no formato das comunidades de WhatsApp e deixa pronto para enviar com 1 clique.

- Documentação completa: [`RELATORIO.md`](RELATORIO.md)
- Fluxograma (o que é automático e o que é manual): [`FLUXOGRAMA.md`](FLUXOGRAMA.md)
- Avaliação de qualidade/segurança: [`RELATORIO_QA_v2.md`](RELATORIO_QA_v2.md)

## Testar agora (Windows, modo demonstração — sem credenciais da Amazon)

Precisa só do **Python 3.11 ou mais novo** ([python.org/downloads](https://www.python.org/downloads/) — marque **"Add python.exe to PATH"** na instalação).

No PowerShell, dentro da pasta do projeto:

```powershell
cd "C:\PROGRAMAÇÃO\PESSOAL\Promocao-afiliado\promo-radar"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1      # se o PowerShell bloquear: Set-ExecutionPolicy -Scope Process RemoteSigned
python -m pip install --upgrade pip
pip install -r requirements.lock
pip install pytest                  # opcional, para rodar os testes
Copy-Item .env.example .env
notepad .env                        # defina PANEL_PASSWORD (mínimo 8 caracteres) e salve
python -m app.cli serve
```

Abra **http://127.0.0.1:8000**, entre com `admin` e a senha que você definiu, e clique em **Coletar agora**. Com `CATALOG_MODE=mock` os produtos são fictícios: serve para conhecer a fila, a revisão e a tela de envio sem gastar nada.

Para testar o envio de verdade, crie um grupo só seu no WhatsApp e mande o post para lá antes de usar na comunidade.

Para parar: `Ctrl + C`. Para rodar de novo depois, repita só as duas últimas linhas (ative o `.venv` e rode o `serve`).

Conferir se está tudo íntegro: `pytest -q` (66 testes).

## Com a Amazon de verdade

No `.env`: `CATALOG_MODE=creators`, `AMAZON_PARTNER_TAG` (sua tag, termina em `-20`), `AMAZON_CREDENTIAL_ID`, `AMAZON_CREDENTIAL_SECRET`, `AMAZON_CREDENTIAL_VERSION` (3.1 para o Brasil).
As credenciais saem do Associates Central → Ferramentas → Creators API. Para ter acesso é preciso ter **10 vendas qualificadas nos últimos 30 dias**.

Em `config/niches.yaml`, ajuste para cada comunidade: `name`, `whatsapp_target` (só um rótulo para você saber onde enviar), as buscas (`searches`) e os filtros (`min_discount_pct`, faixa de preço, janela de horário).

As buscas usam as **categorias da própria Amazon** (`search_index`) — veja a lista com `python -m app.cli categorias`. No amazon.com.br são 10, e nomes comuns em outros países (`Fashion`, `Toys`…) não existem aqui: nesses casos use `search_index: All` com palavras-chave ou um `browse_node_id` (o número de `node=` na URL da categoria no site). Categoria inválida impede o sistema de subir, com a lista das válidas na mensagem.

Opcionais: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (aviso no celular quando entra post novo) e `ANTHROPIC_API_KEY` (chamadas escritas por IA; sem a chave, usa as frases do `niches.yaml`).

## Docker

```bash
cp .env.example .env    # defina PANEL_PASSWORD
docker compose up -d --build
```

O painel fica em `http://127.0.0.1:8000`, acessível só desta máquina. Para abrir em outro aparelho, use VPN (ex.: Tailscale) ou um proxy com HTTPS e `COOKIE_SECURE=true`.

## Testes

```bash
pytest -q              # 73 testes
ruff check app tests && mypy app
```
