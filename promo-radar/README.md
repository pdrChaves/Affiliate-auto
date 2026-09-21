# Promo Radar

Você pesquisa um termo ("teclado", "mochila"), o sistema busca na Amazon pela Creators API, valida o desconto, monta o post no formato das comunidades de WhatsApp e deixa pronto para enviar com 1 clique. Buscas salvas rodam sozinhas.

- Documentação completa: [`RELATORIO.md`](RELATORIO.md)
- Fluxograma (o que é automático e o que é manual): [`FLUXOGRAMA.md`](FLUXOGRAMA.md)
- Avaliação de qualidade/segurança: [`RELATORIO_QA_v3.md`](RELATORIO_QA_v3.md)

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

Abra **http://127.0.0.1:8000**, entre com `admin` e a senha que você definiu, e **pesquise** na barra do topo (ex.: `teclado`, `fone de ouvido`, `headset gamer`). A tela de resultados mostra, item a item, se a oferta passa nas regras e como o post ficaria; clique em **Colocar na fila** no que interessar, ou em **Salvar esta busca** para ela rodar sozinha. Com `CATALOG_MODE=mock` os produtos são fictícios: serve para conhecer a busca, a fila, a revisão e a tela de envio sem gastar nada.

Para testar o envio de verdade, crie um grupo só seu no WhatsApp e mande o post para lá antes de usar na comunidade.

Para parar: `Ctrl + C`. Para rodar de novo depois, repita só as duas últimas linhas (ative o `.venv` e rode o `serve`).

Conferir se está tudo íntegro: `pytest -q` (92 testes).

## Com a Amazon de verdade

No `.env`: `CATALOG_MODE=creators`, `AMAZON_PARTNER_TAG` (sua tag, termina em `-20`), `AMAZON_CREDENTIAL_ID`, `AMAZON_CREDENTIAL_SECRET`, `AMAZON_CREDENTIAL_VERSION` (3.1 para o Brasil).
As credenciais saem do Associates Central → Ferramentas → Creators API. Para ter acesso é preciso ter **10 vendas qualificadas nos últimos 30 dias**.

Em `config/config.yaml` ficam as regras (`min_discount_pct`, faixa de preço, janela de horário, de quanto em quanto tempo as buscas salvas rodam) e o estilo do post. É um arquivo só: não existe mais "nicho" — você separa o conteúdo **pesquisando**.

Os filtros do painel são os **19 departamentos do menu "Comprar por categoria"** do amazon.com.br, e o departamento de cada produto vem da própria Amazon — não da caixa de seleção da busca. Veja a lista com `python -m app.cli categorias`.

A API aceita só 10 recortes de busca (`searchIndex`). Onde existe equivalente (Livros, Games, Eletrônicos…) a busca usa ele; nos outros (Pet Shop, Roupas, Brinquedos, Beleza, Esportes…) a busca vai em "Todos os departamentos" e o resultado é filtrado pelo departamento do produto. Na prática, você não precisa saber disso: escolhe o departamento na tela e funciona.

Opcionais: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (aviso no celular quando entra post novo) e `ANTHROPIC_API_KEY` (chamadas escritas por IA; sem a chave, usa as frases do `config.yaml`).

## Docker

```bash
cp .env.example .env    # defina PANEL_PASSWORD
docker compose up -d --build
```

O painel fica em `http://127.0.0.1:8000`, acessível só desta máquina. Para abrir em outro aparelho, use VPN (ex.: Tailscale) ou um proxy com HTTPS e `COOKIE_SECURE=true`.

## Testes

```bash
pytest -q              # 92 testes
ruff check app tests && mypy app
```
