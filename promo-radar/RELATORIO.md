# Promo Radar: relatório técnico (v1)

Automação de divulgação de promoções da **Amazon** em **comunidades de WhatsApp**
Data: 19/09/2026 · Versão 1.0

---

## 1. Resumo

**O que o sistema faz sozinho:** busca ofertas na Amazon pela API oficial, descarta promoções fracas ou suspeitas e escreve a chamada do post. Também monta a mensagem com **De x Por**, o link de afiliado, o aviso de publicidade e o carimbo de horário do preço. Depois disso, põe tudo numa fila, confere o preço de novo antes do envio e continua monitorando a oferta depois de enviada, para avisar quando ela acabar.

**O que ele não faz:** apertar "enviar" no WhatsApp. O envio é **1 clique seu** no painel: o botão abre o WhatsApp com o texto pronto, você escolhe a comunidade e envia. Isso foi decidido de propósito, pelos motivos abaixo.

| Por que o envio não é 100% automático | Fonte |
|---|---|
| Os Termos do WhatsApp proíbem "bulk messaging, auto-messaging" e o acesso por meios automatizados ou não autorizados. Libs como Baileys, whatsapp-web.js e Evolution API automatizam uma conta comum, que é exatamente o caso proibido. O risco concreto é o **banimento do número que administra as comunidades**. | WhatsApp Terms of Service |
| A via oficial (WhatsApp Cloud API → Groups API) permite **no máximo 8 participantes por grupo**, entra por convite, exige Official Business Account e **não fala com Comunidades**. Não resolve o caso de uso. | Meta for Developers, Groups API |

A camada de envio fica isolada em `app/senders/`. Se um canal oficial viável aparecer, a troca fica restrita a esse módulo.

---

## 2. Termos e regras: o que foi lido e como o sistema cumpre

### 2.1 Programa de Associados Amazon.com.br (Políticas + Licença)

| Regra (resumo fiel do texto) | Como o sistema atende | Onde |
|---|---|---|
| Preço e disponibilidade **só podem ser exibidos** se vierem da API (Creators API) ou de link fornecido pela Amazon | Por padrão, o preço vem só do Creators API. No post manual (sem API), o preço **fica oculto** enquanto `ALLOW_MANUAL_PRICES=false` | `service.show_prices`, `render.py` |
| Com atualização menos frequente que 1×/hora, exibir **carimbo de data/hora** junto ao preço + aviso de que pode mudar | Todo post leva "🕒 Preço verificado em DD/MM às HH:MM. Preço e disponibilidade podem mudar." Ao clicar **Enviar**, se o preço tiver mais de 60 min (`MAX_PRICE_AGE_MINUTES`), ele é **revalidado** e o texto refeito | `render.py`, `service.prepare_send` |
| Conteúdo de produto pode ficar em cache por **no máximo 24h**. Imagens nunca podem ser armazenadas (só o link, por 24h). **Só o ASIN** pode ser guardado sem limite | Imagem nunca é baixada: o card do WhatsApp busca a imagem direto da Amazon. Fila com mais de 24h expira, e um expurgo de hora em hora apaga título/preço/texto de posts antigos, mantendo só ASIN, nicho, status e datas. **Por isso não existe histórico próprio de preços** (ver 7.2) | `service.expire_stale_queue`, `service.purge_old_content`, `db.purge_product_content` |
| Proibido disfarçar/obscurecer a URL. Encurtador que esconda que o destino é a Amazon também é proibido | Link canônico e visível: `https://www.amazon.com.br/dp/ASIN?tag=SEUTAG-20`. O painel recusa link manual que não seja amazon.com.br com `tag=` | `amazon/base.affiliate_link`, `web/server.manual` |
| Não incluir, excluir ou modificar o Conteúdo do Programa (exceto truncar texto ou redimensionar imagem) | O título do produto **só é truncado** na palavra, com "…". A chamada criativa ("PRA TREINAR NO CONFORTO") é conteúdo seu e fica **separada** do título | `render.truncate_title`, `copywriter.py` |
| Remover o link quando a promoção acabar | O monitor revisa por 48h cada post enviado. Se o preço subir ou o produto sair de estoque, o post vai para **Encerrados** e você recebe um alerta para apagar a mensagem ("apagar para todos" só funciona por cerca de 2 dias) | `service.monitor_sent` |
| Links em mensagens diretas são permitidos se a comunicação for **solicitada** | Comunidade em que o membro entra por vontade própria = solicitada. **Não use o sistema para mandar no privado de quem não pediu** | Operacional |
| Proibido gerar cliques ou sessões artificiais por software | O sistema não clica em links nem abre páginas da Amazon. Só consulta a API | Arquitetura |
| Marcas da Amazon só para indicar disponibilidade no site. Nada de logo em rede social | O post não usa logo, só o texto "Amazon" para dizer onde está o produto. **Não use o logo da Amazon na foto/nome da comunidade** | Operacional |
| Sem conteúdo enganoso | Filtros de desconto mínimo, alerta de "De" suspeito e revisão humana antes de todo envio | `scorer.py` |

**Acesso ao Creators API:** o PA-API 5 foi descontinuado em 30/04/2026 e desligado em 15/05/2026. O substituto, o Creators API, exige **10 vendas qualificadas nos últimos 30 dias**. Ver a seção 10 (fase inicial).

### 2.2 Publicidade no Brasil

| Regra | Como atende |
|---|---|
| **CONAR** (Guia de Publicidade por Influenciadores, atualizado em 2026): conteúdo que gera comissão precisa ser identificado como publicidade **de forma imediata**, sem o leitor ter que clicar em "ver mais", e com termos em português (#publi, #publicidade) | A **primeira linha** de todo post é `#publi · link de afiliado Amazon` |
| **CDC art. 37**: publicidade enganosa, incluindo "De/Por" maquiado | O "De" é o preço de referência informado pela própria Amazon (`savingBasis`). Quando ele é preço de tabela (`LIST_PRICE`) ou o desconto passa de 75%, o post entra na fila **com aviso** para você conferir. Dá para bloquear preço de tabela por nicho (`accept_list_price: false`) |

**Recomendação operacional:** deixe na descrição de cada comunidade algo como: "Comunidade de ofertas com links de afiliado: posso receber comissão por compras qualificadas, sem custo extra para você."

> Aviso: isto é leitura técnica das regras, não parecer jurídico. As políticas mudam, então releia as da Amazon a cada poucos meses (links na seção 14).

---

## 3. Arquitetura

```
                 ┌──────────────────────────── processo único (Python) ────────────────────────────┐
                 │                                                                                  │
 Amazon          │  Agendador (APScheduler)                                                         │
 Creators API ◄──┼── collect(nicho) a cada N min ─► Scorer ─► Copywriter ─► Render ─► Fila (SQLite)   │
 (OAuth2)        │                                   │           │                        │         │
                 │                                   │     (IA opcional:                  │         │
                 │                                   │      só a chamada)                 ▼         │
                 │  monitor_sent (1h) ◄─────────────────────────────────────────── Painel web       │
                 │  expire_stale_queue (30min)                                    (FastAPI)        │
                 │  purge_old_content (1h)                                           │              │
                 │           │                                                       │ "Enviar"     │
                 │           ▼                                                       ▼              │
                 │  Telegram (opcional: alerta p/ VOCÊ)                 revalida preço se > 60 min  │
                 └──────────────────────────────────────────────────────────────────┬───────────────┘
                                                                                    │ wa.me/?text=...
                                                                                    ▼
                                                                   WhatsApp (você escolhe a comunidade
                                                                   e envia; o card com a imagem é gerado
                                                                   pelo próprio WhatsApp a partir do link)
```

**Ciclo de vida de um post:**

```
pending ──aprovar──► approved ──enviar/já enviei──► sent ──(preço subiu / sem estoque)──► ended
   │                     │                              └──(48h depois)──► conteúdo expurgado
   ├──descartar──► rejected
   └──(>24h na fila ou oferta morreu na revalidação)──► expired
```

---

## 4. Stack e por quê

| Escolha | Motivo | Alternativa descartada |
|---|---|---|
| **Python 3.11+** | Bom ecossistema para HTTP, agendamento e testes. Código curto e legível para evoluir | Node: também serviria, sem vantagem clara aqui |
| **FastAPI + Jinja2** (painel server-side) | Sem build de front-end e roda num único processo. O painel é simples (fila + botões) | SPA React: complexidade sem ganho na v1 |
| **SQLite** (`sqlite3` da stdlib) | Zero infraestrutura, um arquivo só, com backup via cópia. O volume é pequeno (dezenas de posts/dia) | Postgres: só vale com vários processos ou máquinas. A troca fica restrita a `db.py` |
| **APScheduler** dentro do mesmo processo | Menos peças para operar | Cron + worker separado: mais peças para manter |
| **httpx** | Cliente HTTP com mocks nativos (`MockTransport`), usado nos testes da API | requests |
| **Docker Compose** | Deploy idêntico em PC, VPS ou Raspberry | — |
| IA (Claude API) **opcional**, só para a chamada | Gera variedade de texto. Com regras rígidas e validação, cai no fallback se violar | IA escrevendo o post inteiro: arriscaria alterar dados do produto (proibido) e inventar características |

Dependências de runtime: 9 pacotes (`requirements.txt`).

---

## 5. Módulos

```
promo-radar/
├── app/
│   ├── config.py            # .env (Settings) + niches.yaml (Niche); endpoints de token por versão
│   ├── models.py            # Offer (snapshot do produto), PostStatus; valores em centavos (int)
│   ├── db.py                # SQLite: posts, watchlist, runs; expurgo de conteúdo
│   ├── service.py           # ORQUESTRAÇÃO: collect, refresh, prepare_send, monitor, expirar, expurgar
│   ├── scheduler.py         # rotinas automáticas
│   ├── app_factory.py       # monta as peças (injeção de dependência simples)
│   ├── cli.py               # serve | collect | monitor | purge | preview
│   ├── amazon/
│   │   ├── base.py          # contrato CatalogClient + link de afiliado canônico
│   │   ├── creators.py      # cliente Creators API: OAuth2, getItems (lotes de 10), searchItems, retry/backoff
│   │   ├── mock.py          # catálogo simulado no MESMO formato JSON da API (usa o mesmo parser)
│   │   └── ratelimit.py     # limite de requisições/segundo
│   ├── pipeline/
│   │   ├── scorer.py        # regras de aceitação + nota (ordenação da fila)
│   │   ├── copywriter.py    # chamada do post: IA opcional com validação → fallback do nicho
│   │   └── render.py        # texto no formato WhatsApp, R$ brasileiro, link wa.me
│   ├── senders/
│   │   ├── base.py          # contrato Notifier (ponto de troca do canal)
│   │   └── telegram.py      # alertas para o operador via bot oficial
│   └── web/
│       ├── server.py        # rotas do painel (Basic Auth)
│       └── templates/       # base, index (fila), send (envio)
├── config/niches.yaml       # um bloco por comunidade
├── tests/                   # 29 testes (pytest)
├── Dockerfile, docker-compose.yml, .env.example, README.md
```

---

## 6. Modelo de dados (SQLite)

| Tabela | Campos principais | Observação |
|---|---|---|
| `posts` | id, niche_id, **asin**, status, headline, text, price_cents, basis_cents, discount_pct, score, offer_json, price_checked_at, created_at, approved_at, sent_at, ended_at, note | Título, preço e texto são expurgados depois de 24h (não enviados) ou 49h (enviados) |
| `watchlist` | niche_id, asin, added_at | Só ASIN (pode ficar guardado sem prazo) |
| `runs` | niche_id, started_at, fetched, queued, rejected_json, error | Log das coletas. Motivos de descarte agregados; nenhum conteúdo de produto |

---

## 7. Regras de validação (scorer)

### 7.1 Motivos de descarte (aparecem em "Últimas coletas")

| Código | Significado |
|---|---|
| `sem_preco` | A API não devolveu preço |
| `fora_de_estoque` | Indisponível |
| `nao_novo` | Oferta não é produto novo |
| `nao_buybox` | A oferta não é a vencedora da buy box (o cliente veria outro preço ao clicar) |
| `faixa_de_preco` | Fora de `min_price`/`max_price` do nicho |
| `sem_preco_de` | Sem preço de referência. O post exige "De x Por" |
| `desconto_baixo` | Abaixo de `min_discount_pct` |
| `de_eh_preco_de_tabela` | "De" é preço de tabela e o nicho está com `accept_list_price: false` |
| `ja_na_fila` | Produto já está pendente naquele nicho |
| `cooldown` | Enviado há menos de `cooldown_hours`, a menos que tenha caído mais `repost_if_drop_pct`% |
| `excedeu_limite_por_rodada` | Passou de `max_posts_per_run` (entram só as melhores notas) |

**Nota** = % de desconto, com +5 se tiver selo de oferta (ex.: Oferta Relâmpago), −10 se o "De" for preço de tabela e −15 se o desconto for maior ou igual a 75%.

### 7.2 Limitação importante: não existe histórico próprio de preços

O jeito mais forte de pegar "De" maquiado seria comparar com o histórico de preços, como faz o Keepa. **A Licença não permite guardar preço por mais de 24h.** Por isso o sistema:
- confia no `savingBasis` da Amazon e diferencia `WAS_PRICE` (preço anterior praticado) de `LIST_PRICE` (preço sugerido, mais sujeito a inflar);
- manda casos duvidosos para a fila **com aviso** em vez de publicar direto.

A revisão humana antes do envio é a última barreira. Se quiser histórico, a saída que respeita as regras é usar uma fonte licenciada para isso (ex.: API paga do Keepa, com os termos dela). Não é gravar os dados da Amazon.

---

## 8. Formato do post

Baseado nos prints das duas comunidades (Radar do Homem e LEGO):

```
#publi · link de afiliado Amazon

*PRA TREINAR NO CONFORTO*

Tênis Under Armour Hooper Masculino Corrida

~De R$ 599,00~
*Por R$ 316,00* 🔥 (-47%)
⚡ Oferta Relâmpago                 ← só se a Amazon informar selo
🎟️ Cupom: *MELHORCUPOM*            ← só se você preencher

🛒 Compre aqui:
https://www.amazon.com.br/dp/B0XXXXXXX?tag=seutag-20

📦 Vendido por Amazon.com.br
🕒 Preço verificado em 19/09 às 14:32. Preço e disponibilidade podem mudar.
```

- `*texto*` = negrito e `~texto~` = riscado no WhatsApp.
- **A imagem** aparece no card de pré-visualização que o próprio WhatsApp gera a partir do link. É o mesmo mecanismo dos prints (o `meli.la` embaixo da foto). Isso evita baixar e reenviar a imagem da Amazon, o que a Licença proíbe. **Espere o card carregar antes de enviar.**
- Emoji do preço e frases de chamada são configurados por nicho (`style` no `niches.yaml`).

---

## 9. Operação diária

1. O agendador coleta sozinho (`collect_every_minutes` por nicho). Se o Telegram estiver configurado, chega um aviso quando entra post novo, só dentro de `posting_window`.
2. No painel (aba **Fila**), revise: avisos 📝 em amarelo pedem atenção. Dá para **trocar a chamada**, **adicionar cupom**, **revalidar preço** ou **descartar**.
3. **Enviar →** revalida o preço (se tiver mais de 60 min) → **Abrir no WhatsApp** → escolha a comunidade → espere o card → enviar → volte e clique em **Já enviei**.
4. Se a oferta acabar em até 48h, o post aparece em **Encerrados** e chega um alerta. Se ainda der tempo, apague a mensagem na comunidade.
5. **Watchlist:** cole o ASIN de produtos que você quer acompanhar. Eles são checados a cada coleta e só viram post quando atingem os critérios do nicho.

Rotinas automáticas:

| Rotina | Frequência | Função |
|---|---|---|
| `collect:<nicho>` | por nicho (60/90 min no exemplo) | busca + watchlist → fila |
| `monitor_sent` | 1h | detecta fim da promoção em posts enviados (48h) |
| `expire_stale_queue` | 30 min | expira itens com mais de 24h na fila |
| `purge_content` | 1h | expurga conteúdo de produto antigo (regra das 24h) |

Custo de API por coleta: 1 chamada por busca configurada + 1 a cada 10 ASINs da watchlist. Com os exemplos (3 buscas por nicho, coleta a cada 60–90 min), são cerca de 100 chamadas/dia, bem abaixo do que se espera de cota. A Amazon não publica a cota do Creators API na documentação pública; ajuste `AMAZON_RPS` se ela informar outra.

---

## 10. Fase inicial: antes das 10 vendas (sem Creators API)

Esse é o ponto mais fraco do plano, e é bom encarar de frente: **sem API não há preço conforme as regras**, e o post com "De x Por" que você pediu depende dela.

Caminho sugerido:
1. Use a aba **Post manual** do painel. Com `ALLOW_MANUAL_PRICES=false`, o post sai com "💰 Confira o preço atualizado no link". O link vem do SiteStripe (barra de Associados no próprio site da Amazon).
2. Divulgue a comunidade e cresça até as 10 vendas em 30 dias.
3. Gere as credenciais no Associates Central, troque para `CATALOG_MODE=creators` e o fluxo completo liga.

Existe `ALLOW_MANUAL_PRICES=true`, porque é o que muita comunidade faz na prática. Mas ele **viola a letra da política** (preço digitado não é "obtido pela API"). Ativar é decisão sua, sabendo do risco de ter a conta de Associado encerrada.

Outro ponto a ter em mente: a Amazon pode **revogar o acesso à API** se as vendas caírem abaixo do mínimo. Se isso acontecer, as coletas começam a registrar erro na tabela de coletas, e o painel continua funcionando no modo manual.

---

## 11. Configuração

### `.env`

| Variável | Padrão | Uso |
|---|---|---|
| `CATALOG_MODE` | `mock` | `mock` (demonstração) ou `creators` (real) |
| `AMAZON_PARTNER_TAG` | — | sua tag, ex.: `pedro-20` |
| `AMAZON_MARKETPLACE` | `www.amazon.com.br` | |
| `AMAZON_CREDENTIAL_ID` / `_SECRET` | — | Associates Central → Creators API |
| `AMAZON_CREDENTIAL_VERSION` | `3.1` | Brasil = grupo North America (3.1; credencial antiga 2.1) |
| `AMAZON_TOKEN_URL` | automático | sobrescreve o endpoint de token |
| `AMAZON_RPS` | `1` | requisições/segundo |
| `MAX_PRICE_AGE_MINUTES` | `60` | idade máxima do preço no momento do envio |
| `ALLOW_MANUAL_PRICES` | `false` | ver seção 10 |
| `CONTENT_RETENTION_HOURS` | `24` | **não aumente**: é o limite da Licença |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | vazio | IA para as chamadas (opcional) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | vazio | alertas para você (opcional) |
| `PANEL_USER` / `PANEL_PASSWORD` | admin / troque… | **troque antes de expor** |

### `config/niches.yaml` (1 bloco por comunidade)

`searches` (palavra-chave + `search_index`, ou `browse_node_id`), `watchlist`, `min_discount_pct`, `min_price`/`max_price`, `require_buybox`, `accept_list_price`, `cooldown_hours`, `repost_if_drop_pct`, `max_posts_per_run`, `posting_window`, `collect_every_minutes`, `style` (emoji, chamadas de reserva, tom para a IA).
**Novo nicho** = copiar um bloco e reiniciar. Nenhuma linha de código muda.

---

## 12. Instalação e deploy

- **Local:** ver `README.md` (venv → `python -m app.cli serve`).
- **Docker:** `docker compose up -d --build`. Os dados ficam em `./data`.
- **24/7:** qualquer VPS pequena (1 vCPU, 512 MB) ou um PC ligado. O consumo é mínimo.
- **Segurança:** o painel usa Basic Auth, que **sem HTTPS manda a senha em texto aberto**. Se for expor na internet, ponha atrás de um proxy com TLS (Caddy faz isso sozinho) ou acesse por VPN/Tailscale. Nunca versione o `.env`.
- **Backup:** copiar `data/promo.db`.

---

## 13. Testes e verificação

`pytest -q` → **29 testes, todos passando.** Cobrem:
- formatação (R$ brasileiro, riscado, `#publi` na primeira linha, carimbo, truncagem sem reescrita, link transparente);
- todas as regras do scorer, incluindo cooldown e repost com queda de preço;
- fluxo completo do serviço: coleta sem duplicar, chamadas sem repetição, **revalidação de preço velho no envio**, **bloqueio de envio quando a oferta morreu**, monitor marcando promoção encerrada, expiração e expurgo das 24h, post manual com preço oculto ou exibido;
- cliente Creators API com HTTP simulado: OAuth v3 (Basic + `creatorsapi::default`) e v2 (`Version 2.1`), cache do token, retry em 429, lotes de 10 ASINs, parser em camelCase e PascalCase, preferência pela buy box;
- IA: validação das chamadas (bloqueia números, %, "menor preço"…) e fallback;
- painel: autenticação, coleta, troca de chamada, cupom, tela de envio com `wa.me`, confirmação.

Também verifiquei visualmente o painel (desktop e celular) com o servidor rodando em modo mock.

**O que NÃO foi testado:** chamadas reais ao Creators API, porque não tenho credenciais. Endpoints, autenticação e nomes de campos vêm da documentação oficial (guia de migração, GetItems, SearchItems) e de um SDK open-source que já implementa o Creators API. O parser foi feito tolerante a maiúsculas e minúsculas e a campos ausentes. **No primeiro uso real**, rode `python -m app.cli collect` e confira a saída e a tabela "Últimas coletas" antes de confiar no agendador.

---

## 14. Limitações, riscos e premissas

| Item | Risco | Mitigação |
|---|---|---|
| Envio manual | Exige você presente para publicar | 1 clique por post + alerta no Telegram. É o preço de não arriscar o número |
| Requisito de 10 vendas/30 dias | Sem API, sem preço | Seção 10 |
| Esquema do Creators API | Campos podem divergir levemente do documentado | Parser tolerante + teste manual no primeiro uso |
| Valores de `savingBasisType` | Só `LIST_PRICE` é tratado de forma especial; outros valores passam como preço anterior | Revisão humana; ajuste em `scorer.py` |
| Sem histórico de preços | "De" inflado pode passar se a própria Amazon o validar | Avisos de desconto alto e de preço de tabela + revisão |
| Card do link no WhatsApp | O WhatsApp pode não gerar o card às vezes (cache ou lentidão) | Esperar carregar. Reenviar o link se não aparecer |
| Mensagem antiga com preço velho | Não dá para editar depois de ~15 min nem apagar depois de ~2 dias | Carimbo de horário + monitor por 48h |
| Políticas mudam | Regra nova pode quebrar a conformidade | Revisar as políticas periodicamente |

---

## 15. Como evoluir (onde mexer)

| Mudança desejada | Arquivo(s) |
|---|---|
| Novo nicho/comunidade | `config/niches.yaml` |
| Layout do post | `app/pipeline/render.py` (+ testes em `tests/test_render.py`) |
| Critérios de promoção | `app/pipeline/scorer.py` |
| Outra IA ou prompt | `app/pipeline/copywriter.py` |
| Outro e-commerce (ex.: Mercado Livre, Shopee) | novo cliente em `app/amazon/` seguindo `CatalogClient` (vale renomear o pacote para `catalog/`) + regras daquele programa de afiliados |
| Canal de envio oficial (se surgir) | `app/senders/` |
| Postgres / multiusuário | `app/db.py` |
| Agendamento de horário de envio, métricas de cliques | não existem na v1. Cliques e vendas só vêm dos relatórios do Associates Central (a Amazon não expõe isso por API pública de forma simples) |

---

## 16. Fontes consultadas

- [Políticas do Programa de Associados Amazon.com.br](https://associados.amazon.com.br/help/operating/policies/)
- [Creators API: introdução e requisitos](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/introduction)
- [Creators API: migração a partir do PA-API](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/migrating-to-creatorsapi-from-paapi)
- [Creators API: GetItems](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/api-reference/operations/get-items) · [SearchItems](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/api-reference/operations/search-items)
- [PA-API v5: desligamento em 30/04 e 15/05/2026 (DEV Community)](https://dev.to/th3nate/amazon-pa-api-v5-is-shutting-down-april-30-2026-here-is-what-changes-at-the-auth-layer-22ek)
- [goark/pa-api: SDK do Creators API (endpoints de token, escopos, versões)](https://github.com/goark/pa-api)
- [WhatsApp: Termos de Serviço](https://www.whatsapp.com/legal/terms-of-service)
- [Meta: Groups API (WhatsApp Cloud API)](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups)
- [CONAR: Guia 2026 para influenciadores e afiliados (resumo na Central Shopee)](https://help.shopee.com.br/portal/10/article/196794-Identifica%C3%A7%C3%A3o-de-Conte%C3%BAdo-Publicit%C3%A1rio:-Guia-CONAR-2026-para-Influenciadores-e-Afiliados)
