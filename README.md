# ⚖️ Produtividade Jurídica — Automação de Prazos

[![CI](https://github.com/Dangerdrive/produtividade-juridica/actions/workflows/ci.yml/badge.svg)](https://github.com/Dangerdrive/produtividade-juridica/actions/workflows/ci.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Lê e-mails de protocolo no Gmail, extrai prazos processuais com **LLM (DeepSeek)** e
apresenta tudo num dashboard web — **Python + FastAPI, pronto para deploy na Vercel**.

> 📘 Planejamento técnico completo (arquitetura, modelo de dados, máquina de estados,
> limites e roadmap): [`docs/PLANEJAMENTO.md`](docs/PLANEJAMENTO.md)
>
> 🗄️ O código antigo em Google Apps Script foi arquivado em [`legacy/apps_script/`](legacy/apps_script/)
> e **não é mais usado**.

---

## Como funciona

1. **Ingestão determinística (sem IA)** — busca `in:inbox is:unread`, grava remetente
   (nome/e-mail), assunto, data de recebimento e IDs da mensagem. O **número do processo é
   extraído do assunto por regex CNJ** (`0100138-89.2023.5.01.0074`), incluindo variações
   com espaços ou separadores ausentes. `COMMIT` feito aqui.
2. **Enriquecimento por IA** — envia o corpo ao `deepseek-chat` com
   `response_format: json_object` e recebe `{"prazos": [...]}`. Os processos achados no
   assunto vão como dica no prompt.
3. **Normalização** — datas e números de processo são revalidados no Python
   (regex CNJ, datas impossíveis, deduplicação). O que não passa vira `REVISAR`.
4. **Estado final** — marca o e-mail como lido **somente** em caso de sucesso.
5. **Espelhamento** — os prazos são replicados numa planilha Google Sheets, que tem um
   **Dashboard em Apps Script** (tabela dinâmica).
6. **Dashboard web** — matriz Advogado × Tipo de Prazo, distribuições e próximos vencimentos.

```mermaid
flowchart LR
    A[Gmail API<br/>OAuth2] -->|mensagens| B[Ingestão<br/>sem IA]
    B -->|commit| DB[(Postgres / SQLite)]
    B -->|corpo do e-mail| C[DeepSeek<br/>deepseek-chat]
    C -->|JSON| D[Normalização<br/>+ validação]
    D -->|prazos| DB
    DB --> E[FastAPI<br/>dashboard + API JSON]
    F[Vercel Cron] --> G[/api/cron/sync/]
    G --> A
```

---

## Estrutura

```
.
├── api/index.py              # entrypoint ASGI exigido pela Vercel
├── app/
│   ├── main.py               # FastAPI, middleware, lifespan
│   ├── config.py             # Settings (pydantic-settings)
│   ├── db.py                 # engine/sessão + migração leve de colunas
│   ├── models.py             # Email, Prazo, SyncState
│   ├── cli.py                # CLI de exploração (python -m app.cli)
│   ├── deps.py               # token de sincronização
│   ├── templating.py         # Jinja2 + filtros de data
│   ├── routers/              # pages, api (JSON), sync (cron/manual/planilha)
│   ├── services/
│   │   ├── gmail.py          # REST do Gmail + OAuth2 (httpx)
│   │   ├── deepseek.py       # cliente do modelo + prompt
│   │   ├── extracao.py       # regex: nº do processo, datas, tipos de prazo
│   │   ├── normalizacao.py   # validação de datas/processos
│   │   ├── ingest.py         # orquestração + idempotência
│   │   ├── sheets.py         # espelho em Google Sheets (conta de serviço)
│   │   └── reports.py        # agregações do dashboard
│   ├── templates/            # dashboard.html, prazos.html
│   └── static/styles.css
├── apps_script/              # dashboard (tabela dinâmica) na planilha
│   ├── Codigo.gs
│   └── appsscript.json
├── scripts/
│   ├── authorize_gmail.py    # gera GMAIL_REFRESH_TOKEN
│   ├── init_db.py            # cria schema (+ --demo)
│   └── sync_cli.py           # sincronização pelo terminal
├── tests/                    # 135 testes (pytest)
├── legacy/apps_script/       # versão antiga (arquivada)
├── requirements.txt
└── vercel.json
```

---

## Rodando localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env          # preencha as credenciais
python scripts/init_db.py --demo
uvicorn app.main:app --reload
```

Abra <http://127.0.0.1:8000>. Com `--demo` você já vê o dashboard populado.

### 1. Credenciais do Gmail (OAuth2)

1. No [Google Cloud Console](https://console.cloud.google.com/), crie um projeto e ative a
   **Gmail API**.
2. `APIs e serviços → Credenciais → Criar credenciais → ID do cliente OAuth`
   - Tipo: **Aplicativo para computador**
   - URI de redirecionamento: `http://localhost:8080/`
3. Gere o refresh token:

```bash
python scripts/authorize_gmail.py \
  --client-id SEU_CLIENT_ID \
  --client-secret SEU_CLIENT_SECRET \
  --write-env
```

O script sobe um servidor local temporário para capturar o `code` e grava
`GMAIL_REFRESH_TOKEN` no `.env`.

### 2. DeepSeek

Crie a chave em <https://platform.deepseek.com/api_keys> e defina `DEEPSEEK_API_KEY` no `.env`.

### 3. Sincronizar

```bash
# rotina contínua (não lidos)
python scripts/sync_cli.py --modo continuo

# carga histórica (60 dias, retomável)
python scripts/sync_cli.py --modo historico --limite 100
```

Ou via HTTP:

```bash
curl -X POST "http://127.0.0.1:8000/api/sync?modo=historico" \
  -H "Authorization: Bearer $SYNC_TOKEN"
```

---

## CLI de exploração

Serve para inspecionar e-mails reais e **iterar nas regras de extração** sem subir o servidor.

```bash
# lista mensagens com o nº de processo detectado no assunto
python -m app.cli emails --limite 10

# abre uma mensagem: cabeçalho, extração determinística e corpo
python -m app.cli abrir 18c0ffee --corpo 4000

# testa as regras num texto avulso (sem Gmail)
python -m app.cli testar \
  --assunto "Intimação - Processo 0100138-89.2023.5.01.0074" \
  --corpo "Prazo de 15 dias para contrarrazões até 20/11/2026."

# mesma coisa, lendo de arquivo e chamando o modelo
echo "$TEXTO" | python -m app.cli testar --ia
python -m app.cli testar --arquivo email.txt --ia

# modo interativo (l = listar, a <id> = abrir, t = testar texto)
python -m app.cli explorar

# o que já foi processado
python -m app.cli banco --status REVISAR
```

---

## Planilha Google Sheets + Dashboard em Apps Script

O banco Postgres é a **fonte da verdade**; a planilha é uma **projeção** que alimenta o
dashboard em Apps Script (a tabela dinâmica que você já conhece).

```mermaid
flowchart LR
    DB[(Postgres)] -->|sincronizar_planilha| SH["Sheets: aba DB_Prazos"]
    SH -->|tabela dinâmica| DASH["Aba Dashboard (Apps Script)"]
```

### 1. Criar a conta de serviço

1. Google Cloud Console → **APIs e serviços → Ativar APIs** → habilite a **Google Sheets API**.
2. **Credenciais → Criar credenciais → Conta de serviço**.
3. Na conta criada: **Chaves → Adicionar chave → JSON**. Guarde o arquivo.
4. Copie o campo `client_email` (algo como `bot@projeto.iam.gserviceaccount.com`).

### 2. Compartilhar a planilha

Abra a planilha → **Compartilhar** → cole o `client_email` da conta de serviço como **Editor**.

### 3. Configurar

```bash
# .env  (ou variáveis na Vercel)
GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account", ... }'   # conteúdo do JSON
GOOGLE_SHEET_ID=1AbC...                                            # id da planilha
GOOGLE_SHEET_TAB=DB_Prazos
SHEETS_SYNC_ATIVO=true
```

> Aceita o JSON cru (em uma linha) ou em **base64** — útil em painéis que não lidam bem
> com quebras de linha.

### 4. Enviar os dados

O espelhamento acontece automaticamente ao final de cada sincronização. Para forçar:

```bash
python -m app.cli planilha
curl -X POST "http://localhost:8000/api/sync/sheets" -H "Authorization: Bearer $SYNC_TOKEN"
```

É **idempotente**: a coluna `A` guarda o `ID do Prazo`, então reexecutar não duplica —
só atualiza as linhas que mudaram.

### 5. Ligar o dashboard

1. Abra a planilha → **Extensões → Apps Script**.
2. Crie `Codigo.gs` com [`apps_script/Codigo.gs`](apps_script/Codigo.gs) e ajuste o manifesto.
3. Rode `configurarPlanilha()` uma vez (autoriza os escopos).
4. Recarregue a planilha: menu **⚖️ Produtividade Jurídica → Atualizar Dashboard**.

A aba `Dashboard` é **apagada e recriada** a cada atualização — trate-a como descartável.

### Layout da aba `DB_Prazos`

| A | B | C | D | E | F | G | H | I | J | K | L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ID do Prazo | Número do Processo | Advogado | Tipo de Prazo | Data do Prazo | Status | Data Recebimento | Remetente (E-mail) | Remetente (Nome) | Assunto | Gmail Message ID | Atualizado Em |

A tabela dinâmica usa: linhas = `Advogado`, colunas = `Tipo de Prazo`,
valores = `COUNTA(Número do Processo)`, filtro = `Data do Prazo`.

---

## Deploy na Vercel

### 1. Banco Postgres

A Vercel não tem disco persistente — **SQLite não funciona em produção**. Use
[Neon](https://neon.tech), [Supabase](https://supabase.com) ou Vercel Postgres e copie a
connection string:

```
DATABASE_URL=postgresql+psycopg://usuario:senha@host/banco?sslmode=require
```

### 2. Deploy

```bash
npm i -g vercel
vercel login
vercel link

# Variáveis de produção
vercel env add DATABASE_URL production
vercel env add GMAIL_CLIENT_ID production
vercel env add GMAIL_CLIENT_SECRET production
vercel env add GMAIL_REFRESH_TOKEN production
vercel env add DEEPSEEK_API_KEY production
vercel env add SYNC_TOKEN production        # openssl rand -hex 32
vercel env add APP_PASSWORD production      # protege o dashboard

vercel --prod
```

As tabelas são criadas automaticamente no primeiro acesso (`lifespan → init_db`).

### 3. Agendamento

`vercel.json` já declara um Cron Job diário (`0 11 * * *` → `/api/cron/sync`).

> ⚠️ No plano **Hobby** a Vercel só permite **1 execução por dia**. Para rodar a cada
> 15 minutos use um agendador externo (cron-job.org, GitHub Actions) chamando:
> `https://SEU-APP.vercel.app/api/cron/sync?token=$SYNC_TOKEN`
> — ou faça upgrade para Pro e ajuste o `schedule`.

A Vercel envia automaticamente `Authorization: Bearer <CRON_SECRET>` quando essa variável
existe; o endpoint aceita esse cabeçalho, `Bearer $SYNC_TOKEN` ou `?token=`.

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Dashboard (matriz + gráficos) |
| `GET` | `/prazos` | Listagem com filtros e paginação |
| `GET` | `/api/stats` | Agregações em JSON |
| `GET` | `/api/prazos` | Lista paginada em JSON |
| `POST` | `/api/sync` | Sincronização manual (requer token) |
| `GET` | `/api/cron/sync` | Sincronização agendada (requer token) |
| `POST` | `/api/sync/sheets` | Espelha os prazos na planilha (requer token) |
| `GET` | `/health` | Saúde + configuração |
| `GET` | `/docs` | OpenAPI/Swagger |

---

## Modelo de dados

**`emails`** — uma linha por mensagem (ingestão sem IA)

| Coluna | Origem |
|---|---|
| `gmail_message_id` | Gmail (**chave de idempotência**) |
| `gmail_thread_id`, `assunto` | Gmail |
| `remetente_nome`, `remetente_email` | Gmail (`From`) |
| `data_recebimento` | Gmail (`internalDate`) |
| `status`, `erro`, `tentativas`, `processado_em` | Sistema |

**`prazos`** — uma linha por prazo (resultado da IA)

`advogado`, `tipo_prazo`, `data_prazo`, `numero_processo`, `status`, `email_id`.

Assim um e-mail com 3 prazos gera **1** registro em `emails` e **3** em `prazos` — sem
linhas duplicadas como na planilha antiga.

### Status

| Status | Significado | Reprocessa? |
|---|---|---|
| `PENDENTE_IA` | Ingestão gravada, IA ainda não respondeu | ✅ |
| `OK` | Todos os campos essenciais válidos | ❌ |
| `REVISAR` | Extraído, mas falta advogado/data/processo válido | ❌ |
| `SEM_PRAZO` | E-mail sem prazo processual | ❌ |
| `ERRO_IA` | Falha HTTP/rede/cota | ✅ |
| `ERRO_JSON` | Modelo devolveu JSON inválido | ✅ |

---

## Qualidade

```bash
python -m pytest          # 135 testes
ruff check app tests scripts api
```

Cobertura dos testes: regex do número do processo (assunto e corpo), parsing MIME do
Gmail, retry/backoff do cliente DeepSeek, idempotência e máquina de estados da ingestão,
espelhamento no Sheets (API mockada) e todas as rotas HTTP.

---

## Segurança e LGPD

- Nenhuma credencial no código — tudo em variáveis de ambiente.
- `APP_PASSWORD` habilita HTTP Basic no dashboard (dados jurídicos não ficam públicos).
- `SYNC_TOKEN` protege os endpoints de sincronização.
- O corpo do e-mail é truncado em 12.000 caracteres antes de ir para a API.
- O conteúdo dos e-mails é enviado a um terceiro (DeepSeek). Avalie o mascaramento de
  dados pessoais antes do uso em produção (Fase 3 do roadmap).
