# Fluxograma do Promo Radar (v1.1)

O preço é conferido na Amazon no instante do envio, então o post só sai enquanto a promoção está ativa. O acompanhamento depois do envio vem desligado.

Legenda: **azul = automático (sistema)** · **laranja = manual (você)** · **cinza tracejado = serviço externo** · **vermelho = fim do caminho**.
Versão visual completa (abre no navegador): [`docs/fluxograma.html`](docs/fluxograma.html).

```mermaid
flowchart TD
    classDef auto fill:#E2EFF4,stroke:#1D6A86,color:#17211E
    classDef man fill:#FBEAD8,stroke:#A9560D,color:#17211E
    classDef ext fill:#EEF0F0,stroke:#667072,stroke-dasharray:5 4,color:#17211E
    classDef stop fill:#FAE6E3,stroke:#AE2A20,color:#17211E
    classDef dec fill:#FFFFFF,stroke:#1D6A86,color:#17211E

    CFG["👤 Configura .env e niches.yaml<br/>(uma vez)"]:::man --> UP["⚙️ Painel + agendador no ar"]:::auto
    UP --> TICK["⚙️ Agendador dispara a coleta<br/>a cada 60–90 min por nicho"]:::auto
    NOW["👤 Coletar agora / ASIN na watchlist<br/>(opcional)"]:::man -.-> FETCH
    TICK --> FETCH["⚙️ Busca ofertas e preços"]:::auto
    FETCH <-->|consulta| AMZ[("Amazon Creators API")]:::ext
    FETCH --> DAPI{"API respondeu?"}:::dec
    DAPI -->|não| ERR["Registra erro<br/>tenta na próxima coleta"]:::stop
    DAPI -->|sim| VAL["⚙️ Valida cada oferta<br/>estoque · buy box · desconto · 'De' · cooldown"]:::auto
    VAL --> DOK{"Passou nas regras?"}:::dec
    DOK -->|não| DROP["Descarta<br/>motivo em 'Últimas coletas'"]:::stop
    DOK -->|sim| WRITE["⚙️ Escreve o post<br/>#publi · De/Por · link com tag · carimbo"]:::auto
    WRITE -.->|chamada opcional| AI[("Claude API")]:::ext
    WRITE --> QUEUE["⚙️ Entra na fila do painel"]:::auto
    QUEUE -.->|alerta opcional| TG[("Telegram")]:::ext
    MAN["👤 Post manual (sem API)<br/>link gerado · preço oculto"]:::man --> QUEUE
    QUEUE --> REVIEW["👤 Revisa no painel<br/>chamada · cupom · aprovar · descartar"]:::man
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
| Configurar senha, tag, credenciais e nichos | Você | uma vez |
| Buscar ofertas na Amazon (busca por nicho + watchlist) | Sistema | a cada 60–90 min |
| Validar desconto, estoque, buy box, "De" e repetição | Sistema | a cada coleta |
| Escrever o post (#publi, De/Por, link, carimbo) | Sistema | a cada coleta |
| Avisar que há post novo | Sistema | dentro da janela do nicho |
| Revisar (chamada, cupom, aprovar, descartar) | Você | quando quiser |
| Conferir o preço na Amazon antes de liberar o link | Sistema | sempre, no clique |
| Escolher a comunidade e enviar no WhatsApp | Você | 1 clique por post |
| Confirmar "Já enviei" | Você | após enviar |
| Monitorar a oferta e alertar quando acabar *(desligado por padrão)* | Sistema | a cada 1h, por 48h |
| Apagar a mensagem de promoção encerrada *(só com o monitor ligado)* | Você | até ~2 dias |
| Expirar fila velha, expurgar conteúdo, limpar banco | Sistema | 30 min · 1h · diário |
