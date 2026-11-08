# ⚖️ Produtividade Jurídica — Plataforma Modular

[![CI](https://github.com/Dangerdrive/produtividade-juridica/actions/workflows/ci.yml/badge.svg)](https://github.com/Dangerdrive/produtividade-juridica/actions/workflows/ci.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Plataforma modular de automação jurídica em **Python + FastAPI**, pronta para
deploy na Vercel. O aplicativo original de prazos virou apenas **um dos módulos**:

| Módulo | Responsabilidade |
|---|---|
| `prazos` | Lê e-mails de protocolo no Gmail, extrai prazos com LLM (DeepSeek) e apresenta dashboards. |
| `google` | Camada única de integração com Gmail, Google Sheets e Google Docs. |
| `repasses` | Controla repasses numa planilha própria, lista tarefas pendentes e gera links de status por cliente. |

> 📘 Planejamento técnico original: [`docs/PLANEJAMENTO.md`](docs/PLANEJAMENTO.md)
>
> 🗄️ O código antigo em Google Apps Script foi arquivado em [`legacy/apps_script/`](legacy/apps_script/)
> e **não é mais usado**.

```mermaid
flowchart TB
    subgraph Plataforma
        CORE[Núcleo<br/>config · banco · templates · auth]
        P[prazos]
        G[google]
        R[repasses]
    end

    P --> G
    R --> G
    CORE --> P
    CORE --> R

    G --> GMAIL[Gmail API]
    G --> SHEETS[Google Sheets]
    G --> DOCS[Google Docs]
```

---

## Módulo de prazos — como funciona

O módulo `prazos` mantém o fluxo original:

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
    F[Vercel Cron] --> G[/api/prazos/cron/sync/]
    G --> A
```

---

## Estrutura

```
.
├── api/index.py                  # entrypoint ASGI exigido pela Vercel
├── app/
│   ├── main.py                   # FastAPI: middleware + montagem dos módulos
│   ├── cli.py                    # CLI (prazos, repasses e links de cliente)
│   ├── core/                     # infraestrutura compartilhada
│   │   ├── config.py             # Settings (pydantic-settings)
│   │   ├── db.py                 # engine/sessão + migração leve
│   │   ├── deps.py               # tokens de autenticação
│   │   ├── modulos.py            # registro dos módulos
│   │   ├── router.py             # página inicial + /health
│   │   └── templating.py         # Jinja2 multi-módulo + filtros
│   ├── modules/
│   │   ├── google/               # integração Google (sem regra de negócio)
│   │   │   ├── auth.py           # conta de serviço → access token (cache por escopo)
│   │   │   ├── gmail.py          # OAuth2 + REST do Gmail
│   │   │   ├── sheets.py         # Sheets API v4 genérica
│   │   │   ├── docs.py           # Docs API v1 (ler, criar, anexar, substituir)
│   │   │   ├── router.py         # /google + /api/google/*
│   │   │   └── templates/status.html
│   │   ├── prazos/               # o app original, agora isolado
│   │   │   ├── models.py         # Email, Prazo, SyncState
│   │   │   ├── services/         # deepseek, extracao, normalizacao,
│   │   │   │                     # ingest, reports, espelho_sheets
│   │   │   ├── routers/          # pages, api, sync
│   │   │   └── templates/        # dashboard.html, prazos.html
│   │   └── repasses/             # planilha de repasses + status por cliente
│   │       ├── modelos.py        # Repasse, StatusRepasse, resumos
│   │       ├── colunas.py        # mapeamento de cabeçalhos (com aliases)
│   │       ├── tokens.py         # links individuais (HMAC por cliente)
│   │       ├── deps.py
│   │       ├── services/         # planilha.py, dominio.py
│   │       ├── routers/          # api.py, pages.py
│   │       └── templates/        # painel interno + página do cliente
│   ├── templates/                # base compartilhada + home
│   └── static/styles.css
├── apps_script/                  # dashboard (tabela dinâmica) na planilha de prazos
├── scripts/
│   ├── authorize_gmail.py        # gera GMAIL_REFRESH_TOKEN
│   ├── init_db.py                # cria schema (+ --demo)
│   ├── sync_cli.py               # sincronização pelo terminal
│   └── token_repasses.py         # gera links de status por cliente
├── tests/                        # 173 testes (pytest)
├── legacy/apps_script/           # versão antiga (arquivada)
├── requirements.txt
└── vercel.json
```

---

## Módulo `google` — integração única

O módulo `google` concentra todo o acesso ao ecossistema Google. Os demais módulos
consomem seus clientes; nenhum deles fala diretamente com as APIs do Google.

| Cliente | Autenticação | Operações |
|---|---|---|
| `GmailClient` | OAuth2 (refresh token do usuário) | listar, obter, marcar como lida, enviar, parsing MIME/HTML |
| `SheetsClient` | Conta de serviço | ler, escrever, anexar, limpar, atualizar linhas, cabeçalho, metadados, criar planilha |
| `DocsClient` | Conta de serviço | ler texto, criar documento, anexar texto, substituir trechos |

A autenticação de conta de serviço (`auth.py`) assina o JWT RS256 uma vez e faz cache
do access token **por escopo** — Sheets e Docs compartilham a mesma credencial.

### Configuração

```bash
# Opção 1: conteúdo do JSON (cru ou base64)
GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account", ... }'

# Opção 2 (mais prática): caminho para o arquivo baixado do Google Cloud
GOOGLE_SERVICE_ACCOUNT_FILE=/caminho/para/chave.json
```

Compartilhe cada planilha/documento com o `client_email` da conta de serviço como
**Editor**. O Gmail continua usando as credenciais OAuth2
(`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`).

### Endpoints

Todas as rotas de API exigem `Authorization: Bearer $SYNC_TOKEN` (ou `?token=`).

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/google` | Página de status da integração |
| `GET` | `/api/google/status` | Estado em JSON |
| `GET` | `/api/google/gmail/mensagens` | Lista mensagens (`q`, `limite`, `pagina_token`, `detalhar`) |
| `POST` | `/api/google/gmail/mensagens` | Envia uma mensagem (`para`, `assunto`, `corpo`, `cc`, `bcc`, `html`) |
| `GET` | `/api/google/gmail/mensagens/{id}` | Mensagem completa |
| `POST` | `/api/google/gmail/mensagens/{id}/lida` | Remove o rótulo `UNREAD` |
| `GET` | `/api/google/sheets/{sheet_id}/valores` | Lê um intervalo |
| `PUT` | `/api/google/sheets/{sheet_id}/valores` | Sobrescreve um intervalo |
| `POST` | `/api/google/sheets/{sheet_id}/append` | Anexa linhas |
| `GET` | `/api/google/sheets/{sheet_id}/metadados` | Abas e propriedades |
| `POST` | `/api/google/sheets` | Cria uma planilha |
| `GET` | `/api/google/docs/{document_id}` | Texto do documento (`completo=true` inclui a estrutura) |
| `POST` | `/api/google/docs` | Cria um documento |
| `POST` | `/api/google/docs/{document_id}/texto` | Anexa texto ao final |
| `POST` | `/api/google/docs/{document_id}/substituir` | Substitui um trecho |

### Teste rápido (Gmail + Sheets)

```bash
# 1. preencha o .env (GMAIL_* e GOOGLE_SERVICE_ACCOUNT_FILE/JSON)
# 2. leia um e-mail qualquer
.venv/bin/python scripts/test_google.py gmail --query "in:anywhere"

# 3. escreva numa planilha existente compartilhada com a conta de serviço
.venv/bin/python scripts/test_google.py sheets --sheet-id <ID> --aba "Página1"

# 4. ou tente criar uma planilha nova
.venv/bin/python scripts/test_google.py sheets --criar

# tudo de uma vez
.venv/bin/python scripts/test_google.py tudo --sheet-id <ID>
```

> Contas de serviço podem não ter cota no Drive para **criar** arquivos. Se `--criar`
> falhar, crie a planilha manualmente, compartilhe com o `client_email` da conta de
> serviço como Editor e use `--sheet-id`.

---

## Módulo `repasses` — planilha, pendências e status do cliente

O módulo fica atrelado a **uma planilha específica** (`REPASSES_SHEET_ID`) e usa a
conta de serviço do módulo `google`. A planilha é a fonte da verdade.

### Configuração

```bash
REPASSES_SHEET_ID=1AbC...                 # id da planilha de repasses
REPASSES_SHEET_TAB=Repasses               # aba
REPASSES_CLIENTE_TOKEN_SECRET=...         # openssl rand -hex 32
REPASSES_API_TOKEN=...                    # se vazio, usa SYNC_TOKEN
# REPASSES_COLUNAS_JSON={"data_prevista": "Vencimento", "responsavel": "Analista"}
```

### Layout esperado da aba

| A | B | C | D | E | F | G | H | I | J |
|---|---|---|---|---|---|---|---|---|---|
| ID | Cliente | Processo | Valor | Data Prevista | Data do Pagamento | Status | Responsável | Observações | Atualizado Em |

- O mapeamento é feito **por nome de cabeçalho**, tolerante a acentos e maiúsculas.
  Nomes aceitos incluem `Código` para ID, `Sacado` para Cliente, `Vencimento` para
  Data Prevista e `Situação` para Status.
- Colunas fora do padrão podem ser mapeadas com `REPASSES_COLUNAS_JSON`.
- Se a planilha estiver vazia, o cabeçalho padrão é criado automaticamente.
- A coluna `ID` é obrigatória para atualizações. Se ausente, o módulo informa o erro.

### Status

| Status | Significado | Conta como pendente? |
|---|---|---|
| `PENDENTE` | Aguardando pagamento/ação | ✅ |
| `EM_ANDAMENTO` | Em processamento | ✅ |
| `CONCLUIDO` | Pago/finalizado | ❌ |
| `CANCELADO` | Cancelado/estornado | ❌ |

Sinônimos comuns (`pago`, `finalizado`, `em aberto`, `a pagar`, ...) são normalizados.

### Painel interno

- `GET /repasses` — cards de resumo, **tarefas pendentes** ordenadas por vencimento,
  totais por cliente e tabela completa. Cada pendência tem um formulário para atualizar
  status/data de pagamento direto na planilha.
- `GET /repasses/cliente/{cliente}?token=...` — página pública (sem HTTP Basic) com o
  status daquele cliente. O token é um HMAC-SHA256 do nome normalizado do cliente.
- `POST /repasses/{id}/status` — atualização via formulário HTML.

### API interna

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/repasses` | Lista com filtros `status`, `cliente`, `pendentes`, `q` |
| `GET` | `/api/repasses/pendentes` | Tarefas pendentes ordenadas por vencimento |
| `GET` | `/api/repasses/clientes` | Resumo consolidado por cliente |
| `GET` | `/api/repasses/{id}` | Um repasse |
| `POST` | `/api/repasses` | Cria um repasse na planilha |
| `PATCH` | `/api/repasses/{id}` | Atualiza campos (status, valor, datas, responsável, ...) |
| `GET` | `/api/repasses/cliente/{cliente}?token=...` | Status público do cliente |

As rotas internas exigem `REPASSES_API_TOKEN` (ou `SYNC_TOKEN`). A rota pública de
cliente exige o token individual e fica liberada do HTTP Basic para que o cliente
consiga abrir o link.

### Gerar links de cliente

```bash
# um cliente
python scripts/token_repasses.py --cliente "Maria Souza" --base https://seu-app.vercel.app

# todos os clientes da planilha
python scripts/token_repasses.py --todos --base https://seu-app.vercel.app

# equivalente pela CLI
python -m app.cli repasse-token "Maria Souza" --base https://seu-app.vercel.app
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
curl -X POST "http://127.0.0.1:8000/api/prazos/sync?modo=historico" \
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

# repasses: tarefas pendentes
python -m app.cli repasses

# link individual de acompanhamento de um cliente
python -m app.cli repasse-token "Maria Souza" --base https://seu-app.vercel.app
```

---

## Módulo de prazos — Planilha Google Sheets + Dashboard em Apps Script

O banco Postgres é a **fonte da verdade** do módulo de prazos; a planilha é uma
**projeção** que alimenta o dashboard em Apps Script (a tabela dinâmica que você já
conhece). É uma planilha **diferente** da usada pelo módulo de repasses.

```mermaid
flowchart LR
    DB[(Postgres)] -->|sincronizar_planilha| SH["Sheets: aba DB_Prazos"]
    SH -->|tabela dinâmica| DASH["Aba Dashboard (Apps Script)"]
```

### 1. Criar a conta de serviço

1. Google Cloud Console → **APIs e serviços → Ativar APIs** → habilite a **Google Sheets API**
   e a **Google Docs API** (esta última para o módulo `google`).
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
curl -X POST "http://localhost:8000/api/prazos/sync/sheets" -H "Authorization: Bearer $SYNC_TOKEN"
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
vercel env add GOOGLE_SERVICE_ACCOUNT_JSON production
vercel env add GOOGLE_SHEET_ID production          # módulo de prazos
vercel env add REPASSES_SHEET_ID production        # módulo de repasses
vercel env add REPASSES_CLIENTE_TOKEN_SECRET production
vercel env add SYNC_TOKEN production               # openssl rand -hex 32
vercel env add APP_PASSWORD production             # protege as páginas internas

vercel --prod
```

As tabelas são criadas automaticamente no primeiro acesso (`lifespan → init_db`).

### 3. Agendamento

`vercel.json` já declara um Cron Job diário (`0 11 * * *` → `/api/prazos/cron/sync`).

> ⚠️ No plano **Hobby** a Vercel só permite **1 execução por dia**. Para rodar a cada
> 15 minutos use um agendador externo (cron-job.org, GitHub Actions) chamando:
> `https://SEU-APP.vercel.app/api/prazos/cron/sync?token=$SYNC_TOKEN`
> — ou faça upgrade para Pro e ajuste o `schedule`.

A Vercel envia automaticamente `Authorization: Bearer <CRON_SECRET>` quando essa variável
existe; o endpoint aceita esse cabeçalho, `Bearer $SYNC_TOKEN` ou `?token=`.

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Página inicial com os módulos |
| `GET` | `/prazos` | Dashboard de prazos (matriz + gráficos) |
| `GET` | `/prazos/lista` | Listagem com filtros e paginação |
| `GET` | `/api/prazos/stats` | Agregações em JSON |
| `GET` | `/api/prazos` | Lista paginada em JSON |
| `POST` | `/api/prazos/sync` | Sincronização manual (requer token) |
| `GET` | `/api/prazos/cron/sync` | Sincronização agendada (requer token) |
| `POST` | `/api/prazos/sync/sheets` | Espelha os prazos na planilha (requer token) |
| `GET` | `/google` | Status da integração Google |
| `GET` | `/api/google/*` | Gmail, Sheets e Docs (requer token) |
| `GET` | `/repasses` | Painel interno de repasses |
| `GET` | `/repasses/cliente/{cliente}` | Status público do cliente (token individual) |
| `GET/POST/PATCH` | `/api/repasses/*` | API de repasses |
| `GET` | `/health` | Saúde + configuração de cada módulo |
| `GET` | `/docs` | OpenAPI/Swagger |

---

## Módulo de prazos — modelo de dados

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
python -m pytest          # 173 testes
ruff check app tests scripts api
```

Cobertura dos testes: regex do número do processo (assunto e corpo), parsing MIME do
Gmail, retry/backoff do cliente DeepSeek, idempotência e máquina de estados da ingestão,
espelhamento no Sheets (API mockada), cliente genérico de Sheets, Google Docs (API
mockada), leitura/escrita da planilha de repasses, regras de pendências/atrasos,
tokens de cliente e todas as rotas HTTP.

---

## Segurança e LGPD

- Nenhuma credencial no código — tudo em variáveis de ambiente.
- `APP_PASSWORD` habilita HTTP Basic nas páginas internas (dados jurídicos não ficam públicos).
- `SYNC_TOKEN` protege os endpoints do módulo de prazos e da integração Google.
- `REPASSES_API_TOKEN` protege a API interna de repasses (fallback: `SYNC_TOKEN`).
- Links de cliente usam HMAC-SHA256 com `REPASSES_CLIENTE_TOKEN_SECRET`; as rotas
  públicas de cliente e os estáticos ficam liberadas do HTTP Basic.
- O corpo do e-mail é truncado em 12.000 caracteres antes de ir para a API.
- O conteúdo dos e-mails é enviado a um terceiro (DeepSeek). Avalie o mascaramento de
  dados pessoais antes do uso em produção (Fase 3 do roadmap).
