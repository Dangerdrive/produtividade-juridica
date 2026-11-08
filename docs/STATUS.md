# Status do Projeto — 2026-11-08

> Documento de acompanhamento da reestruturação modular e da integração Google.
> Não contém credenciais nem valores sensíveis.

---

## 1. O que foi feito

### 1.1 Reestruturação modular

O projeto deixou de ser um monólito focado apenas em prazos e passou a ser uma
plataforma modular:

| Módulo | Responsabilidade |
|---|---|
| `app/core` | Configuração, banco, dependências, templates, registro de módulos e rotas transversais (`/` e `/health`). |
| `app/modules/prazos` | Aplicação original de prazos: ingestão de e-mails, extração com DeepSeek, banco e dashboards. |
| `app/modules/google` | Integração única com Gmail, Google Sheets e Google Docs. |
| `app/modules/repasses` | Planilha própria de repasses, tarefas pendentes, resumo por cliente e links individuais. |

### 1.2 Módulo Google

- `auth.py`: autenticação por conta de serviço (JWT RS256 + cache de token por escopo).
- `gmail.py`: OAuth2, listar/obter/enviar mensagens, marcar como lida e perfil da conta.
- `sheets.py`: cliente genérico (ler, escrever, anexar, limpar, cabeçalho, metadados, criar planilha).
- `docs.py`: ler texto, criar documento, anexar texto e substituir trechos.
- `contas.py`: suporte a **múltiplas contas Gmail**.
- `router.py`: página `/google` e API `/api/google/*`.

### 1.3 Múltiplas contas Gmail

- A conta única continua funcionando com `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` e `GMAIL_REFRESH_TOKEN`.
- Contas adicionais podem ser declaradas em `GMAIL_ACCOUNTS_FILE` (arquivo JSON) ou `GMAIL_ACCOUNTS_JSON`.
- `GMAIL_CONTA_PADRAO` escolhe a conta usada quando nenhuma é informada.
- O script `authorize_gmail.py` ganhou:
  - `--manual` (não depende de servidor/navegador local);
  - `--contas-file` (grava a conta em um JSON);
  - `--conta` e `--rotulo` (nome e descrição da conta).
- A CLI e a API aceitam `--conta` / `?conta=`.
- O modelo `Email` ganhou a coluna `conta_gmail`, registrando em qual conta a mensagem foi recebida.

### 1.4 Módulo de repasses

- Planilha própria via `REPASSES_SHEET_ID`.
- Mapeamento de colunas por cabeçalho (com aliases) e override via `REPASSES_COLUNAS_JSON`.
- Criação e atualização de repasses na planilha.
- Pendências, atrasos, resumo por cliente e painel interno `/repasses`.
- Link público por cliente com token HMAC (`REPASSES_CLIENTE_TOKEN_SECRET`).
- API `/api/repasses/*`, CLI `repasses` e script `scripts/token_repasses.py`.

### 1.5 Testes e ferramentas

- `scripts/test_google.py`: testa Gmail, Sheets e lista contas Gmail.
- `scripts/token_repasses.py`: gera links de acompanhamento por cliente.
- `scripts/authorize_gmail.py`: geração de refresh token (normal e manual).
- `tests/test_gmail_contas.py`: testes de múltiplas contas.
- **183 testes** passando.
- `ruff check app tests scripts` sem erros.

---

## 2. Estrutura atual

```text
app/
├── core/                     # config, db, deps, templating, módulos, health
├── modules/
│   ├── google/               # auth, gmail, sheets, docs, contas, router
│   ├── prazos/               # models, services, routers, templates
│   └── repasses/             # modelos, colunas, tokens, services, routers, templates
├── templates/                # base, base_publico, home
└── static/styles.css
scripts/
├── authorize_gmail.py
├── test_google.py
├── token_repasses.py
├── init_db.py
└── sync_cli.py
```

---

## 3. Status atual

### 3.1 Funcionando

- **Google Sheets**: teste de escrita/leitura confirmado pelo usuário.
- **Conta de serviço**: configurada (`GOOGLE_SERVICE_ACCOUNT_FILE` definido).
- **Planilha de prazos**: `GOOGLE_SHEET_ID` definido.
- **Dashboard e APIs de prazos**: rotas montadas e testadas.
- **Testes**: 183 passando; lint limpo.

### 3.2 Pendente de configuração

| Item | Status |
|---|---|
| `GMAIL_REFRESH_TOKEN` | **vazio** — Gmail ainda não autenticado. |
| `GMAIL_ACCOUNTS_FILE` / `GMAIL_ACCOUNTS_JSON` | vazio (nenhuma conta adicional). |
| `GMAIL_CONTA_PADRAO` | vazio. |
| `REPASSES_SHEET_ID` | vazio — módulo de repasses ainda sem planilha. |
| `REPASSES_CLIENTE_TOKEN_SECRET` | vazio — links de cliente desabilitados. |
| `REPASSES_API_TOKEN` | vazio (usa `SYNC_TOKEN`, que está definido). |
| `SYNC_TOKEN` | definido. |
| `APP_PASSWORD` | definido. |

### 3.3 Autorização do Gmail em andamento

O cliente OAuth já tem `GMAIL_CLIENT_ID` e `GMAIL_CLIENT_SECRET`. Falta trocar o
código de autorização pelo refresh token.

- O fluxo manual foi iniciado no terminal (`authorize_gmail.py --manual`).
- Se o código expirar, rode novamente:

```bash
.venv/bin/python scripts/authorize_gmail.py --write-env --manual
```

- Depois, valide:

```bash
.venv/bin/python scripts/test_google.py gmail --query "in:anywhere"
```

---

## 4. Tarefas pendentes

### 4.1 Concluir a autenticação do Gmail

1. Abrir a URL exibida pelo script.
2. Autorizar a conta desejada.
3. Copiar a URL de retorno (`http://localhost:8080/?...&code=...`) e colar no
   terminal no prompt `URL/código:`.
4. O script grava `GMAIL_REFRESH_TOKEN` no `.env`.
5. Rodar o teste do Gmail.

> Se aparecer `redirect_uri_mismatch`, adicionar `http://localhost:8080/` às
> **URIs de redirecionamento autorizados** do cliente OAuth no Google Cloud.

### 4.2 Adicionar uma segunda conta Gmail

Pré-requisito: a primeira conta já autenticada e o cliente OAuth com a URI
`http://localhost:8080/` cadastrada.

```bash
.venv/bin/python scripts/authorize_gmail.py \
  --contas-file gmail_contas.json \
  --conta trabalho \
  --rotulo "Conta do escritório" \
  --write-env --manual
```

- O script grava a conta em `gmail_contas.json` e define
  `GMAIL_ACCOUNTS_FILE` no `.env`.
- A segunda conta pode reutilizar o mesmo `client_id`/`client_secret`; se usar
  outro cliente OAuth, passe `--client-id` e `--client-secret`.
- O arquivo `gmail_contas.json` está no `.gitignore`.

Validar:

```bash
.venv/bin/python scripts/test_google.py contas
.venv/bin/python scripts/test_google.py gmail --conta trabalho --query "in:anywhere"
```

Sincronizar prazos com a conta escolhida:

```bash
.venv/bin/python -m app.cli sincronizar --conta trabalho
```

Ou via API:

```text
POST /api/prazos/sync?conta=trabalho
GET  /api/prazos/cron/sync?conta=trabalho
```

Para tornar a segunda conta a padrão:

```env
GMAIL_CONTA_PADRAO=trabalho
```

### 4.3 Configurar o módulo de repasses

1. Criar/escolher a planilha de repasses.
2. Compartilhar com o `client_email` da conta de serviço como **Editor**.
3. Definir no `.env`:

```env
REPASSES_SHEET_ID=ID_DA_PLANILHA
REPASSES_SHEET_TAB=Repasses
REPASSES_CLIENTE_TOKEN_SECRET=<openssl rand -hex 32>
```

4. Testar a escrita:

```bash
.venv/bin/python scripts/test_google.py sheets --sheet-id ID_DA_PLANILHA
.venv/bin/python -m app.cli repasses
```

5. Se a planilha usar nomes de colunas diferentes, configurar
   `REPASSES_COLUNAS_JSON`.

Layout padrão esperado:

```text
ID | Cliente | Processo | Valor | Data Prevista | Data do Pagamento |
Status | Responsável | Observações | Atualizado Em
```

### 4.4 Testar Google Docs

Ainda não foi executado um teste real de Docs. Sugestão:

```text
GET  /api/google/docs/{document_id}
POST /api/google/docs
POST /api/google/docs/{document_id}/texto
POST /api/google/docs/{document_id}/substituir
```

Lembrar de compartilhar o documento com o `client_email` da conta de serviço.

### 4.5 Produção (Vercel)

- Usar Postgres (`DATABASE_URL`), pois SQLite não persiste na Vercel.
- Configurar todas as variáveis no painel da Vercel.
- O cron em `vercel.json` aponta para `/api/prazos/cron/sync`.
- Rotacionar `SYNC_TOKEN`, `APP_PASSWORD` e os segredos de repasses.
- Garantir que `app/modules/**/templates/**` esteja no `includeFiles`.

---

## 5. Comandos úteis

```bash
# Ambiente
uv venv .venv
uv pip install -p .venv/bin/python -r requirements-dev.txt

# Qualidade
.venv/bin/python -m ruff check app tests scripts
.venv/bin/python -m pytest

# Google
.venv/bin/python scripts/test_google.py contas
.venv/bin/python scripts/test_google.py gmail --conta trabalho --query "in:anywhere"
.venv/bin/python scripts/test_google.py sheets --sheet-id ID_DA_PLANILHA

# Prazos
.venv/bin/python scripts/init_db.py --demo
.venv/bin/python -m app.cli sincronizar --conta trabalho
.venv/bin/python -m app.cli repasses

# Links de repasses
.venv/bin/python scripts/token_repasses.py --todos --base https://seu-app.vercel.app
```

---

## 6. Segurança

- `.env`, `gmail_contas.json` e JSONs de conta de serviço não devem ser commitados.
- Nunca colar chaves privadas, refresh tokens ou códigos OAuth em issues/chat.
- Códigos OAuth são de uso único e expiram; se um código for exposto, basta
  gerar outro fluxo de autorização.
- Os links de cliente usam HMAC e não exigem HTTP Basic; as demais páginas
  internas usam `APP_PASSWORD`.
- A API de repasses usa `REPASSES_API_TOKEN` (ou `SYNC_TOKEN`).

---

## 7. Observações conhecidas

- Contas de serviço podem não ter cota de Drive para **criar** planilhas. Se
  `--criar` falhar, crie a planilha manualmente e compartilhe com o
  `client_email`.
- Em modo de teste do Google OAuth, o refresh token pode expirar em 7 dias.
  Para uso contínuo, publique o app ou use uma conta de serviço/Workspace
  conforme o caso.
- O banco local recebe a coluna `conta_gmail` automaticamente via
  `garantir_colunas_novas()`; em produção, o mesmo acontece no primeiro startup.
