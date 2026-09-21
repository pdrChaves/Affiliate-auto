# Fluxograma do Promo Radar (v1.4)

Agora tudo gira em torno da **barra de pesquisa**: você digita um termo ("teclado", "mochila"), vê o resultado já avaliado e escolhe o que entra na fila. Uma busca que você salva passa a rodar sozinha. O preço é conferido na Amazon no instante do envio, então o post só sai enquanto a promoção está ativa. O acompanhamento depois do envio vem desligado.

Legenda: **azul = automático (sistema)** · **laranja = manual (você)** · **cinza tracejado = serviço externo** · **vermelho = fim do caminho**.
Versão visual completa (abre no navegador): [`docs/fluxograma.html`](docs/fluxograma.html).

```mermaid
flowchart TD
    classDef auto fill:#E2EFF4,stroke:#1D6A86,color:#17211E
    classDef man fill:#FBEAD8,stroke:#A9560D,color:#17211E
    classDef ext fill:#EEF0F0,stroke:#667072,stroke-dasharray:5 4,color:#17211E
    classDef stop fill:#FAE6E3,stroke:#AE2A20,color:#17211E
    classDef dec fill:#FFFFFF,stroke:#1D6A86,color:#17211E

    CFG["👤 Configura .env e config.yaml<br/>(uma vez)"]:::man --> UP["⚙️ Painel + agendador no ar"]:::auto

    UP --> SEARCH["👤 Pesquisa na barra do painel<br/>ex.: 'teclado' + categoria (opcional)"]:::man
    UP --> TICK["⚙️ Agendador roda as buscas salvas<br/>+ a watchlist, a cada 60 min"]:::auto

    SEARCH --> FETCH["⚙️ Consulta a Amazon e avalia cada oferta<br/>estoque · buy box · desconto · 'De' · cooldown"]:::auto
    TICK --> FETCH
    FETCH <-->|consulta| AMZ[("Amazon Creators API")]:::ext
    FETCH --> DAPI{"API respondeu?"}:::dec
    DAPI -->|não| ERR["Registra erro em 'Últimas buscas'<br/>tenta na próxima rodada"]:::stop

    DAPI -->|sim, veio da sua pesquisa| RESULT["👤 Vê cada item com o veredito<br/>'passa nas regras · nota X' ou o motivo<br/>+ prévia do post. NADA é gravado ainda"]:::man
    RESULT --> PICK{"O que você faz?"}:::dec
    PICK -->|Colocar na fila| WRITE
    PICK -->|Salvar esta busca| SAVED["⚙️ Busca salva: roda sozinha<br/>de tempos em tempos"]:::auto
    PICK -->|nada| NOOP["Sai da tela<br/>nenhum registro criado"]:::stop
    SAVED --> TICK

    DAPI -->|sim, veio de busca salva| DOK{"Passou nas regras?"}:::dec
    DOK -->|não| DROP["Descarta<br/>motivo em 'Últimas buscas'"]:::stop
    DOK -->|sim| WRITE["⚙️ Escreve o post<br/>#publi · De/Por · link com tag · carimbo"]:::auto
    WRITE -.->|chamada opcional| AI[("Claude API")]:::ext
    WRITE --> QUEUE["⚙️ Entra na fila do painel"]:::auto
    QUEUE -.->|alerta opcional| TG[("Telegram")]:::ext
    MAN["👤 Post manual (sem API)<br/>link gerado · preço oculto"]:::man --> QUEUE
    WATCH["👤 Cola um ASIN na watchlist"]:::man --> TICK

    QUEUE --> REVIEW["👤 Revisa no painel, sem recarregar a página<br/>chamada · cupom · aprovar · descartar<br/>filtros: chips de categoria + busca por texto"]:::man
    QUEUE -.->|+24h sem ação| EXP["⚙️ Expira"]:::auto
    REVIEW --> SEND["👤 Clica em Enviar"]:::man
    SEND --> REVAL["⚙️ Confere o preço na Amazon<br/>sempre, no clique"]:::auto
    REVAL --> DRV{"Resultado?"}:::dec
    DRV -->|promoção caiu| DEAD["Expira · envio bloqueado"]:::stop
    DRV -->|válida · ou API fora com aviso| WADO["👤 Abre no WhatsApp,<br/>escolhe a comunidade e envia"]:::man
    WADO --> WA[("WhatsApp · comunidade")]:::ext
    WADO --> DONE["👤 Clica em Já enviei"]:::man
    DONE -.->|só com MONITOR_SENT_ENABLED=true| MON["⚙️ Opcional: monitora por 48h<br/>desligado por padrão"]:::auto
    MON --> DEND{"Promoção acabou?"}:::dec
    DEND -.->|não| MON
    DEND -->|sim| ENDED["⚙️ Marca Encerrado + alerta"]:::auto
    ENDED --> DEL["👤 Apaga a mensagem<br/>se ainda der (~2 dias)"]:::man

    CLEAN["⚙️ Em paralelo: expurgo 24h/49h ·<br/>limpeza diária · VACUUM aos domingos"]:::auto
```

## Quem faz o quê

| Etapa | Quem | Quando |
|---|---|---|
| Configurar senha, tag, credenciais e as regras do `config.yaml` | Você | uma vez |
| **Pesquisar um termo na barra** e escolher o que entra na fila | Você | quando quiser |
| Avaliar cada oferta da pesquisa e mostrar o veredito + prévia | Sistema | na hora da pesquisa |
| Rodar as **buscas salvas** e a watchlist | Sistema | a cada 60 min (`saved_search_every_minutes`) |
| Validar desconto, estoque, buy box, "De" e repetição | Sistema | em toda busca |
| Escrever o post (#publi, De/Por, link, carimbo) | Sistema | ao entrar na fila |
| Avisar que há post novo | Sistema | dentro da `posting_window` |
| Revisar (chamada, cupom, aprovar, descartar) — sem recarregar a página | Você | quando quiser |
| Filtrar a fila pelos 19 departamentos do site ou por texto | Você | quando quiser |
| Conferir o preço na Amazon antes de liberar o link | Sistema | sempre, no clique |
| Escolher a comunidade e enviar no WhatsApp | Você | 1 clique por post |
| Confirmar "Já enviei" | Você | após enviar |
| Monitorar a oferta e alertar quando acabar *(desligado por padrão)* | Sistema | a cada 1h, por 48h |
| Apagar a mensagem de promoção encerrada *(só com o monitor ligado)* | Você | até ~2 dias |
| Expirar fila velha, expurgar conteúdo, limpar banco | Sistema | 30 min · 1h · diário |

## O que mudou em relação à v1.1

| Antes (nicho) | Agora (busca) |
|---|---|
| Lista fixa de nichos no `niches.yaml`, cada um com suas buscas e filtros | Um `config.yaml` só, com as regras valendo para tudo |
| O sistema coletava sozinho e você descobria o que ele achou | Você **pesquisa**, vê o resultado avaliado e decide item a item |
| Categorias inventadas que não existem no amazon.com.br | Os **19 departamentos do menu do site**, com o departamento vindo do próprio produto (`browseNodeInfo`) |
| Nenhuma forma de procurar algo pontual | Barra de pesquisa: "teclado", "mochila", "headset gamer" |
| Botão "Coletar agora" | "Salvar esta busca" (roda sozinha) + "Rodar buscas salvas" (força a rodada) |
