#!/usr/bin/env python3
"""Testa a integração Google de ponta a ponta.

Exemplos:
    # lê a mensagem mais recente da caixa de entrada
    python scripts/test_google.py gmail

    # escreve numa planilha existente (compartilhada com a conta de serviço)
    python scripts/test_google.py sheets --sheet-id 1AbC... --aba "Página1"

    # cria uma planilha nova e escreve nela
    python scripts/test_google.py sheets --criar

    # usa a planilha já configurada em GOOGLE_SHEET_ID ou REPASSES_SHEET_ID
    python scripts/test_google.py sheets

    # faz os dois testes
    python scripts/test_google.py tudo --sheet-id 1AbC...
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.core.config import Settings, get_settings  # noqa: E402
from app.modules.google.auth import GoogleAuth  # noqa: E402
from app.modules.google.contas import (  # noqa: E402
    ContasError,
    carregar_contas,
    nome_conta_padrao,
)
from app.modules.google.gmail import GmailClient, GmailError  # noqa: E402
from app.modules.google.sheets import SheetsClient, SheetsError  # noqa: E402

SEPARADOR = "─" * 78


def _titulo(texto: str) -> None:
    print(f"\n{SEPARADOR}\n{texto}\n{SEPARADOR}")


def _email_conta_servico(settings: Settings) -> str:
    """E-mail da conta de serviço (não é segredo; é o que deve ser compartilhado)."""
    try:
        return GoogleAuth(settings).credenciais().get("client_email", "")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------
def listar_contas(settings: Settings) -> int:
    _titulo("Contas Gmail configuradas")

    try:
        contas = carregar_contas(settings)
    except ContasError as exc:
        print(f"Erro ao carregar contas: {exc}")
        return 1

    if not contas:
        print(
            "Nenhuma conta configurada.\n"
            "Preencha GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN ou use "
            "GMAIL_ACCOUNTS_FILE/GMAIL_ACCOUNTS_JSON."
        )
        return 1

    padrao = nome_conta_padrao(settings, contas)
    for nome, conta in contas.items():
        marca = "*" if nome == padrao else " "
        estado = "OK" if conta.configurada else "INCOMPLETA"
        print(
            f"{marca} {nome:20s} | {conta.descricao:30s} | "
            f"query={conta.query!r} | {estado}"
        )
    return 0


def testar_gmail(
    settings: Settings, *, query: str | None, limite: int, conta: str | None
) -> int:
    _titulo("Gmail — leitura de mensagem")

    if not settings.gmail_configurado:
        print(
            "Gmail não configurado.\n"
            "Preencha GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET e GMAIL_REFRESH_TOKEN no .env.\n"
            "Gere o refresh token com:\n"
            "  python scripts/authorize_gmail.py --write-env\n\n"
            "Para mais de uma conta, use GMAIL_ACCOUNTS_FILE ou GMAIL_ACCOUNTS_JSON."
        )
        return 1

    try:
        cliente = GmailClient(settings, conta=conta)
        perfil = cliente.perfil()
        pagina = cliente.listar_mensagens(query, max_results=limite)
        if not pagina.ids:
            print(f"Nenhuma mensagem encontrada para a query: {cliente.conta.query!r}")
            print('Tente: --query "in:anywhere"')
            return 0

        mensagem = cliente.obter_mensagem(pagina.ids[0])
    except GmailError as exc:
        print(f"Erro ao consultar o Gmail: {exc}")
        print("Contas disponíveis: use `python scripts/test_google.py contas`.")
        return 1

    print(f"Conta     : {cliente.nome_conta} ({cliente.conta.descricao})")
    print(f"E-mail    : {perfil.get('email', '(desconhecido)')}")
    print(f"Query     : {query or cliente.conta.query}")
    print(f"ID        : {mensagem.id}")
    print(f"Thread    : {mensagem.thread_id}")
    print(f"Data      : {mensagem.data_recebimento:%d/%m/%Y %H:%M} UTC")
    print(f"Remetente : {mensagem.remetente_nome} <{mensagem.remetente_email}>")
    print(f"Assunto   : {mensagem.assunto}")
    print("\n--- corpo (primeiros 1.000 caracteres) ---")
    print(mensagem.corpo[:1000] or "(corpo vazio)")
    return 0


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------
def _descobrir_aba(cliente: SheetsClient) -> str | None:
    metadados = cliente.obter_metadados()
    abas = [
        (aba.get("properties") or {}).get("title")
        for aba in metadados.get("sheets") or []
    ]
    return next((aba for aba in abas if aba), None)


def testar_sheets(
    settings: Settings,
    *,
    sheet_id: str | None,
    aba: str | None,
    criar: bool,
    titulo: str | None,
) -> int:
    _titulo("Google Sheets — escrita e leitura")

    if not settings.google_configurado:
        print(
            "Conta de serviço não configurada.\n"
            "Defina GOOGLE_SERVICE_ACCOUNT_JSON no .env com o conteúdo do JSON "
            "(cru ou base64) ou GOOGLE_SERVICE_ACCOUNT_FILE com o caminho do arquivo."
        )
        return 1

    email = _email_conta_servico(settings)
    if email:
        print(f"Conta de serviço: {email}")

    sheet_id = sheet_id or settings.google_sheet_id or settings.repasses_sheet_id

    if criar:
        titulo_planilha = titulo or f"Teste Integração {datetime.now():%Y-%m-%d %H:%M}"
        print(f"Criando planilha: {titulo_planilha!r} ...")
        try:
            metadados = SheetsClient(settings).criar_planilha(titulo_planilha)
        except SheetsError as exc:
            print(f"\nFalha ao criar a planilha: {exc}")
            print(
                "\nDica: contas de serviço normalmente não têm cota de Drive para criar\n"
                "arquivos. Crie a planilha manualmente no seu Drive, compartilhe com\n"
                f"{email or 'o client_email da conta de serviço'} como Editor e use:\n"
                "  python scripts/test_google.py sheets --sheet-id <ID_DA_PLANILHA>"
            )
            return 1

        sheet_id = metadados.get("spreadsheetId") or ""
        url = metadados.get("spreadsheetUrl")
        abas = [
            (item.get("properties") or {}).get("title")
            for item in metadados.get("sheets") or []
        ]
        aba = aba or next((nome for nome in abas if nome), None)

        print(f"Planilha criada: {sheet_id}")
        if url:
            print(f"URL: {url}")
    else:
        if not sheet_id:
            print(
                "Nenhuma planilha informada.\n\n"
                "Use uma destas opções:\n"
                "  --sheet-id <ID>     planilha existente compartilhada com a conta de serviço\n"
                "  --criar             cria uma planilha nova (pode falhar por cota do Drive)\n\n"
                "Ou defina GOOGLE_SHEET_ID / REPASSES_SHEET_ID no .env."
            )
            return 1

    cliente = SheetsClient(settings, sheet_id=sheet_id, aba=aba)

    if not aba:
        try:
            aba = _descobrir_aba(cliente)
        except SheetsError as exc:
            print(f"Erro ao ler os metadados da planilha: {exc}")
            print(
                f"Verifique se a planilha {sheet_id} foi compartilhada com "
                f"{email or 'a conta de serviço'} como Editor."
            )
            return 1

    if not aba:
        print("Não foi possível determinar a aba. Informe --aba.")
        return 1

    cliente.aba = aba
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    valores = [
        ["Teste", "Valor", "Data", "Observação"],
        ["Integração OK", "123,45", agora, "Escrito por scripts/test_google.py"],
        ["Linha 2", "678,90", agora, "Pode apagar esta área depois"],
    ]

    try:
        cliente.escrever_valores("A1:D3", valores)
        lidos = cliente.ler_valores("A1:D3")
    except SheetsError as exc:
        print(f"Erro ao escrever/ler a planilha: {exc}")
        print(
            f"\nVerifique se a planilha {sheet_id} foi compartilhada com "
            f"{email or 'a conta de serviço'} como Editor."
        )
        return 1

    print(f"\nEscrito com sucesso em: {aba}!A1:D3")
    print("Leitura de confirmação:")
    print(json.dumps(lidos, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Testa Gmail e Google Sheets com as credenciais do .env"
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_gmail = sub.add_parser("gmail", help="lê uma mensagem do Gmail")
    p_gmail.add_argument(
        "--query", default=None, help="query do Gmail (padrão: GMAIL_QUERY)"
    )
    p_gmail.add_argument("--limite", type=int, default=1)
    p_gmail.add_argument("--conta", default=None, help="nome da conta Gmail")

    sub.add_parser("contas", help="lista as contas Gmail configuradas")

    p_sheets = sub.add_parser("sheets", help="escreve e lê numa planilha")
    p_sheets.add_argument("--sheet-id", default=None, help="ID da planilha")
    p_sheets.add_argument("--aba", default=None, help="nome da aba")
    p_sheets.add_argument(
        "--criar", action="store_true", help="cria uma planilha nova"
    )
    p_sheets.add_argument("--titulo", default=None, help="título da planilha criada")

    p_tudo = sub.add_parser("tudo", help="executa os dois testes")
    p_tudo.add_argument("--query", default=None)
    p_tudo.add_argument("--limite", type=int, default=1)
    p_tudo.add_argument("--conta", default=None)
    p_tudo.add_argument("--sheet-id", default=None)
    p_tudo.add_argument("--aba", default=None)
    p_tudo.add_argument("--criar", action="store_true")
    p_tudo.add_argument("--titulo", default=None)

    args = parser.parse_args(argv)
    settings = get_settings()

    codigo = 0

    if args.comando == "contas":
        return listar_contas(settings)

    if args.comando in ("gmail", "tudo"):
        codigo |= testar_gmail(
            settings,
            query=args.query,
            limite=args.limite,
            conta=args.conta,
        )

    if args.comando in ("sheets", "tudo"):
        codigo |= testar_sheets(
            settings,
            sheet_id=args.sheet_id,
            aba=args.aba,
            criar=args.criar,
            titulo=args.titulo,
        )

    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
