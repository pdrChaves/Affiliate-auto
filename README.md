# Promo Radar

Painel web para quem divulga promoções da Amazon como afiliado em comunidades de WhatsApp.

Você digita um termo de busca ("teclado mecânico", "mochila"), o sistema consulta a Amazon pela Creators API, descarta o que não é promoção de verdade, monta o texto do post no formato usado nas comunidades e deixa tudo numa fila. Você revisa, aprova e envia com um clique. Buscas que você salvar passam a rodar sozinhas de tempos em tempos.

O envio para o WhatsApp é sempre feito por você. O sistema abre o WhatsApp com o texto pronto e você escolhe a comunidade. Isso é proposital: os Termos do WhatsApp proíbem envio automatizado por clientes não oficiais, e a API oficial não atende Comunidades.

## Sumário

- [Como funciona](#como-funciona)
- [Requisitos](#requisitos)
- [Instalação no Windows](#instalação-no-windows)
- [Primeiro uso](#primeiro-uso)
- [Usando com a Amazon de verdade](#usando-com-a-amazon-de-verdade)
- [Configuração](#configuração)
- [Regras de validação](#regras-de-validação)
- [Ciclo de vida de um post](#ciclo-de-vida-de-um-post)
- [Rotinas automáticas](#rotinas-automáticas)
- [Linha de comando](#linha-de-comando)
- [Docker](#docker)
- [Testes](#testes)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Por que algumas coisas são do jeito que são](#por-que-algumas-coisas-são-do-jeito-que-são)

## Como funciona

1. **Busca.** Você pesquisa um termo no painel, opcionalmente dentro de um departamento da Amazon.
2. **Validação.** Cada produto encontrado passa pelas regras definidas em `config/config.yaml`: desconto mínimo, faixa de preço, estoque, se a oferta é a que o cliente vê ao clicar, se já foi postado recentemente.
3. **Prévia.** A tela de resultados mostra, item por item, se passou nas regras (e, se não passou, o motivo) e como o post ficaria.
4. **Fila.** Você coloca na fila o que interessar. Se salvar a busca, ela roda sozinha e enfileira o que passar nas regras.
5. **Revisão.** Na fila você pode editar a chamada do post, adicionar um cupom, atualizar o preço, aprovar ou rejeitar.
6. **Envio.** Ao clicar em Enviar, o sistema confere o preço na Amazon de novo. Se a promoção continua valendo, abre o WhatsApp com o texto pronto. Você escolhe a comunidade, envia e marca o post como enviado no painel.

Exemplo de post gerado:

```
#publi · link de afiliado Amazon

*ACHADO DO DIA*

Teclado Mecânico Gamer RGB Switch Blue ABNT2

~De R$ 299,90~
*Por R$ 189,90* 🔥 (-37%)

🛒 Compre aqui:
https://www.amazon.com.br/dp/B0XXXXXXXX?tag=seutag-20

📦 Vendido por Amazon.com.br
🕒 Preço verificado em 24/09 às 14:32. Preço e disponibilidade podem mudar.
```

## Requisitos

- Python 3.11 ou mais novo. No instalador do [python.org](https://www.python.org/downloads/), marque a opção **Add python.exe to PATH**.
- Para usar produtos reais: conta no Amazon Associados com acesso à Creators API (a Amazon libera depois de 10 vendas qualificadas em 30 dias).
- Opcional: Docker, se preferir rodar em container.

Sem credenciais da Amazon o sistema funciona em modo demonstração, com produtos fictícios.

## Instalação no Windows

### Jeito rápido

Na pasta raiz do repositório, abra o PowerShell e rode:

```powershell
.\iniciar.ps1
```

Na primeira vez, o script cria o ambiente virtual, instala as dependências e gera o arquivo `.env` a partir do exemplo, abrindo-o no Bloco de Notas. Defina `PANEL_PASSWORD` (mínimo de 8 caracteres), salve e feche. O painel sobe em http://127.0.0.1:8000.

Se o PowerShell recusar a execução do script, rode antes:

```powershell
Set-ExecutionPolicy -Scope Process RemoteSigned
```

Nas próximas vezes, `.\iniciar.ps1` só liga o painel. Para parar, `Ctrl + C`.

### Passo a passo manual

```powershell
cd promo-radar
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.lock
Copy-Item .env.example .env
notepad .env          # defina PANEL_PASSWORD e salve
python -m app.cli serve
```

Em Linux ou macOS os comandos são os mesmos, trocando a ativação do ambiente por `source .venv/bin/activate` e a cópia por `cp .env.example .env`.

## Primeiro uso

1. Abra http://127.0.0.1:8000 e entre com o usuário `admin` e a senha definida no `.env`.
2. Pesquise algo na barra do topo, por exemplo `fone de ouvido`.
3. Nos resultados, clique em **Colocar na fila** nos produtos que quiser, ou em **Salvar esta busca** para ela rodar sozinha.
4. Na página inicial, revise a fila: aprove, rejeite, troque a chamada ou adicione um cupom.
5. Clique em **Enviar**. O WhatsApp abre com o texto pronto.

Com `CATALOG_MODE=mock` (o padrão) os produtos são inventados. Serve para conhecer as telas sem chamar a Amazon. Antes de usar numa comunidade de verdade, faça um teste mandando o post para um grupo só seu.

O painel também aceita:

- **Watchlist**: uma lista de ASINs que o sistema confere a cada rodada das buscas salvas. Quando um deles entra em promoção, vai para a fila.
- **Entrada manual**: para quem ainda não tem acesso à Creators API. Você informa ASIN e título, e o sistema monta o link de afiliado com a sua tag. Por padrão o preço não aparece no post (veja `ALLOW_MANUAL_PRICES`).

## Usando com a Amazon de verdade

No `.env`, preencha:

```ini
CATALOG_MODE=creators
AMAZON_PARTNER_TAG=suatag-20
AMAZON_CREDENTIAL_ID=...
AMAZON_CREDENTIAL_SECRET=...
AMAZON_CREDENTIAL_VERSION=3.1
```

As credenciais ficam no Associates Central, em Ferramentas > Creators API. O Brasil usa a versão 3.1 (grupo "North America"). Reinicie o painel depois de alterar o `.env`.

### Departamentos

Os filtros do painel são os 19 departamentos do menu "Comprar por categoria" do amazon.com.br. Para ver a lista:

```powershell
python -m app.cli categorias
```

A Creators API só aceita 10 recortes de busca. Para departamentos que têm equivalente na API (Livros, Games, Eletrônicos e outros), a busca usa esse recorte. Para os demais (Pet Shop, Roupas, Brinquedos, Beleza, Esportes e outros), a busca é feita em todos os departamentos e o resultado é filtrado pelo departamento que a própria Amazon atribui ao produto. Para quem usa o painel, a diferença não aparece: basta escolher o departamento.

## Configuração

A configuração fica em dois arquivos dentro de `promo-radar/`.

### `.env`: credenciais e comportamento do sistema

| Variável | Padrão | Para que serve |
|---|---|---|
| `CATALOG_MODE` | `mock` | `mock` usa produtos fictícios; `creators` usa a Creators API. |
| `AMAZON_PARTNER_TAG` | `seutag-20` | Sua tag de afiliado. Vai em todos os links. |
| `AMAZON_CREDENTIAL_ID` / `AMAZON_CREDENTIAL_SECRET` | vazio | Credenciais da Creators API. |
| `AMAZON_CREDENTIAL_VERSION` | `3.1` | Versão mostrada no Associates Central. |
| `AMAZON_RPS` | `1` | Requisições por segundo à API. Ajuste conforme a sua cota. |
| `MAX_PRICE_AGE_MINUTES` | `0` | Idade máxima do preço na hora de enviar. `0` confere sempre. |
| `MONITOR_SENT_ENABLED` | `false` | Acompanha os posts enviados por 48h e avisa quando a promoção acaba. |
| `ALLOW_MANUAL_PRICES` | `false` | Mostra no post preços digitados à mão. Desligado, o post manda conferir o preço no link. |
| `ANTHROPIC_API_KEY` | vazio | Se preenchida, a chamada do post é escrita por IA. Sem ela, usa as frases do `config.yaml`. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | vazio | Aviso no Telegram quando entram posts novos na fila ou quando uma promoção enviada acaba. |
| `PANEL_USER` | `admin` | Usuário do painel. |
| `PANEL_PASSWORD` | vazio | Obrigatória, mínimo de 8 caracteres. O painel não sobe com senha vazia ou muito comum. |
| `COOKIE_SECURE` | `false` | Use `true` quando o painel estiver atrás de HTTPS. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Onde o painel escuta. `127.0.0.1` restringe o acesso a esta máquina. |
| `DATABASE_PATH` | `data/promo.db` | Arquivo do banco SQLite. |
| `TIMEZONE` | `America/Sao_Paulo` | Fuso usado nos horários do post e na janela de envio. |

### `config/config.yaml`: regras das ofertas e estilo do post

| Chave | Padrão | Significado |
|---|---|---|
| `filters.min_discount_pct` | `20` | Desconto mínimo, em %, informado pela Amazon. |
| `filters.min_price` / `max_price` | `30` / `5000` | Faixa de preço aceita, em reais. |
| `filters.require_buybox` | `true` | Só aceita a oferta que o cliente vê ao abrir o link. |
| `filters.accept_list_price` | `true` | Aceita desconto calculado sobre preço de tabela (entra com aviso). `false` recusa. |
| `filters.cooldown_hours` | `72` | Tempo mínimo antes de postar o mesmo produto de novo. |
| `filters.repost_if_drop_pct` | `5` | Libera repostar antes do prazo se o preço cair pelo menos esse percentual. |
| `filters.max_posts_per_search` | `5` | Quantos posts uma busca salva pode enfileirar por rodada. |
| `filters.posting_window` | `08:00` a `22:30` | Horário em que os avisos do Telegram são enviados. |
| `filters.saved_search_every_minutes` | `60` | Intervalo entre as rodadas das buscas salvas. |
| `style.emoji_price` | `🔥` | Emoji ao lado do preço. |
| `style.whatsapp_target` | vazio | Nome da comunidade, só como lembrete na tela de envio. |
| `style.headline_fallbacks` | lista | Chamadas usadas quando não há IA configurada. |
| `style.tone` | texto | Tom que a IA deve seguir ao escrever a chamada. |

## Regras de validação

Um produto é recusado, com o motivo exibido na tela de resultados, quando:

| Motivo | O que aconteceu |
|---|---|
| `sem_preco` | A Amazon não informou preço. |
| `fora_de_estoque` | Produto indisponível. |
| `nao_novo` | A oferta não é de produto novo. |
| `nao_buybox` | A oferta não é a que aparece para o cliente. |
| `faixa_de_preco` | Preço fora de `min_price` e `max_price`. |
| `sem_preco_de` | Não há preço "De" para comparar. O post precisa mostrar "De X Por Y". |
| `desconto_baixo` | Desconto abaixo de `min_discount_pct`. |
| `de_eh_preco_de_tabela` | O "De" é preço sugerido e `accept_list_price` está em `false`. |
| `ja_na_fila` | O produto já está na fila. |
| `cooldown` | O produto foi postado há menos de `cooldown_hours`. |

Os que passam recebem uma nota usada para ordenar a fila. A nota parte do desconto, perde pontos quando o "De" é preço de tabela ou quando o desconto passa de 75% (o que costuma indicar preço anterior inflado) e ganha pontos quando a Amazon marca a oferta como relâmpago ou similar. Esses casos entram na fila com um aviso para você conferir.

A regra geral é deixar passar uma oferta boa em vez de postar uma promoção falsa.

## Ciclo de vida de um post

| Status | Significado |
|---|---|
| `pending` | Na fila, aguardando revisão. |
| `approved` | Aprovado, pronto para enviar. |
| `sent` | Você confirmou que enviou. |
| `rejected` | Você rejeitou. |
| `expired` | O preço mudou, a oferta sumiu ou o post passou 24h na fila sem ser enviado. |
| `ended` | Já tinha sido enviado, mas a promoção acabou. Só aparece com `MONITOR_SENT_ENABLED=true`. |

Posts `pending` e `approved` podem ser editados. Os demais ficam só para consulta.

## Rotinas automáticas

Rodam dentro do mesmo processo do painel. Enquanto o painel estiver ligado, elas rodam.

| Rotina | Frequência | O que faz |
|---|---|---|
| Buscas salvas | `saved_search_every_minutes` (padrão 60 min) | Roda cada busca salva ativa e a watchlist, e enfileira o que passar nas regras. |
| Expirar fila | a cada 30 min | Marca como `expired` o que está na fila há mais de 24h. |
| Expurgo de conteúdo | a cada 60 min | Apaga título, preço, imagem e texto de posts antigos, mantendo só ASIN, status e datas. |
| Monitoramento | a cada 60 min | Só com `MONITOR_SENT_ENABLED=true`. Confere os posts enviados nas últimas 48h. |
| Limpeza | diária às 04:10 | Remove posts com mais de 90 dias e registros de busca com mais de 30. Aos domingos, compacta o banco. |

## Linha de comando

Todos os comandos rodam dentro de `promo-radar/`, com o ambiente virtual ativado.

```powershell
python -m app.cli serve                          # painel + rotinas automáticas (uso normal)
python -m app.cli categorias                     # lista os departamentos aceitos
python -m app.cli buscar "teclado"               # pesquisa e enfileira o que passar nas regras
python -m app.cli buscar "ração" "Pet Shop"      # mesma coisa, dentro de um departamento
python -m app.cli salvas                         # roda todas as buscas salvas e a watchlist agora
python -m app.cli preview                        # imprime no terminal os posts da fila
python -m app.cli monitor                        # confere promoções já enviadas
python -m app.cli purge                          # roda a expiração, o expurgo e a limpeza agora
```

## Docker

```bash
cd promo-radar
cp .env.example .env      # defina PANEL_PASSWORD
docker compose up -d --build
```

O painel fica em http://127.0.0.1:8000. O `docker-compose.yml` publica a porta só para a própria máquina, monta `data/` para o banco persistir e monta `config/` como somente leitura. O container roda com usuário sem privilégios e sistema de arquivos somente leitura.

Para acessar de outro aparelho, use uma VPN (Tailscale, por exemplo) ou coloque um proxy com HTTPS na frente e defina `COOKIE_SECURE=true`. Não exponha a porta 8000 direto na internet.

## Testes

Da raiz do repositório:

```powershell
.\testar.ps1
```

Ou dentro de `promo-radar/`:

```bash
pip install -r requirements-dev.txt
pytest -q                     # 92 testes
ruff check app tests
mypy app
```

Os testes não chamam a Amazon, o WhatsApp nem o Telegram.

A pasta `promo-radar/qa/` guarda os scripts e os resultados da avaliação de qualidade (carga, disponibilidade, volume, segurança). O resumo está em `promo-radar/RELATORIO_QA_v3.md`.

## Estrutura do projeto

```
.
├── iniciar.ps1                 liga o painel (cria o ambiente na primeira vez)
├── testar.ps1                  roda os testes
└── promo-radar/
    ├── app/
    │   ├── cli.py              comandos de terminal
    │   ├── config.py           leitura do .env e do config.yaml
    │   ├── categories.py       departamentos da Amazon e mapeamento para a API
    │   ├── db.py               banco SQLite
    │   ├── models.py           oferta e status do post
    │   ├── service.py          orquestra busca, validação, fila e envio
    │   ├── scheduler.py        rotinas automáticas
    │   ├── amazon/             cliente da Creators API, catálogo fictício e limite de requisições
    │   ├── pipeline/           validação (scorer), chamada do post (copywriter) e texto final (render)
    │   ├── senders/            notificação pelo Telegram
    │   └── web/                painel FastAPI, login, sessões e templates
    ├── config/config.yaml      regras das ofertas e estilo do post
    ├── tests/                  testes automatizados
    ├── qa/                     scripts e resultados da avaliação de qualidade
    ├── docs/fluxograma.html    fluxograma visual
    ├── .env.example            modelo do .env
    ├── Dockerfile
    └── docker-compose.yml
```

## Por que algumas coisas são do jeito que são

**Envio manual para o WhatsApp.** Os Termos do WhatsApp proíbem mensagens automatizadas por clientes não oficiais. A API oficial só atende grupos de até 8 participantes e não trabalha com Comunidades. O sistema faz todo o preparo e deixa o último clique com você. A parte de envio está isolada em `app/senders/`, caso surja um canal oficial que sirva.

**Dados de produto guardados por no máximo 24h.** A licença da Creators API permite guardar conteúdo de produto (preço, título, imagem) por até 24 horas. Só o ASIN pode ficar guardado sem prazo. Por isso a fila expira em 24h e existe o expurgo automático. Pelo mesmo motivo o sistema não mantém histórico de preços: a checagem de "De" inflado usa o tipo de preço que a própria Amazon informa.

**Título do produto não é reescrito.** A política da Amazon permite só truncar o título, não alterá-lo. A parte criativa do post fica na chamada, que tem regras próprias: nada de números, preços, "grátis", "menor preço" ou urgência inventada.

**Preço conferido na hora do envio.** Com `MAX_PRICE_AGE_MINUTES=0`, todo clique em Enviar consulta a Amazon de novo. Se a promoção acabou, o post expira e o WhatsApp não abre. Se a API estiver fora do ar, o envio é liberado com o preço da última checagem e o horário dela no texto, desde que tenha menos de 24h.

**Marcação `#publi`.** Todo post começa identificando que é publicidade com link de afiliado, como pedem o CONAR e o Código de Defesa do Consumidor.

## Documentação adicional

- [`promo-radar/RELATORIO.md`](promo-radar/RELATORIO.md): documentação técnica detalhada.
- [`promo-radar/FLUXOGRAMA.md`](promo-radar/FLUXOGRAMA.md): o que é automático e o que é manual.
- [`promo-radar/RELATORIO_QA_v3.md`](promo-radar/RELATORIO_QA_v3.md): avaliação de qualidade e segurança.
