"""CLI de exploração e operação.

Objetivo: permitir iterar nas regras de extração (regex do assunto/corpo) sem
subir o servidor.

Uso:
    python -m app.cli emails --limite 10
    python -m app.cli abrir 18c0ffee --corpo 4000
    python -m app.cli testar --assunto "Intimação 0100138-89.2023.5.01.0074"
    python -m app.cli testar --arquivo email.txt --ia
    python -m app.cli explorar
    python -m app.cli banco --status REVISAR
    python -m app.cli planilha
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.modules.prazos.services.extracao import analisar_email, extrair_numeros_processo

SEPARADOR = "─" * 78


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------
def _titulo(texto: str) -> None:
    print(f"\n{SEPARADOR}\n{texto}\n{SEPARADOR}")


def _mostrar_analise(assunto: str, corpo: str, *, como_json: bool = False) -> None:
    analise = analisar_email(assunto, corpo)

    if como_json:
        print(json.dumps(analise.as_dict(), ensure_ascii=False, indent=2))
        return

    print(f"Processos no assunto : {analise.processos or '—'}")
    if analise.datas:
        print("Datas encontradas    : " + ", ".join(d.strftime("%d/%m/%Y") for d in analise.datas))
    if analise.tipos_sugeridos:
        print("Tipos sugeridos      : " + ", ".join(analise.tipos_sugeridos))
    if analise.prazo_dias:
        print(f"Prazo mencionado     : {analise.prazo_dias} dia(s)")


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
def cmd_emails(args) -> int:
    from app.modules.google.gmail import GmailClient, GmailError

    settings = get_settings()
    if not settings.gmail_configurado:
        print("Credenciais do Gmail ausentes. Rode: python scripts/authorize_gmail.py")
        return 1

    cliente = GmailClient(settings, conta=args.conta)

    try:
        pagina = cliente.listar_mensagens(args.query, max_results=args.limite)
    except GmailError as exc:
        print(f"Erro ao consultar o Gmail: {exc}")
        return 1

    if not pagina.ids:
        print(f"Nenhuma mensagem para a query: {args.query}")
        return 0

    _titulo(f"{len(pagina.ids)} mensagem(ns) · query: {args.query}")

    for indice, message_id in enumerate(pagina.ids, start=1):
        try:
            mensagem = cliente.obter_mensagem(message_id)
        except GmailError as exc:
            print(f"{indice:2d}. [{message_id}] erro: {exc}")
            continue

        processos = extrair_numeros_processo(mensagem.assunto)
        marca = processos[0] if processos else "sem processo no assunto"
        print(
            f"{indice:2d}. {mensagem.data_recebimento:%d/%m/%Y %H:%M} "
            f"| {mensagem.remetente_email[:34]:34s} | {marca}"
        )
        print(f"    {mensagem.assunto[:110]}")
        print(f"    id: {message_id}")

    return 0


def cmd_abrir(args) -> int:
    from app.modules.google.gmail import GmailClient, GmailError

    settings = get_settings()
    if not settings.gmail_configurado:
        print("Credenciais do Gmail ausentes. Rode: python scripts/authorize_gmail.py")
        return 1

    cliente = GmailClient(settings, conta=args.conta)

    try:
        mensagem = cliente.obter_mensagem(args.message_id)
    except GmailError as exc:
        print(f"Erro: {exc}")
        return 1

    _titulo("Cabeçalhos")
    print(f"ID        : {mensagem.id}")
    print(f"Thread    : {mensagem.thread_id}")
    print(f"Data      : {mensagem.data_recebimento:%d/%m/%Y %H:%M} (UTC)")
    print(f"Remetente : {mensagem.remetente_nome} <{mensagem.remetente_email}>")
    print(f"Assunto   : {mensagem.assunto}")

    _titulo("Extração determinística (sem IA)")
    _mostrar_analise(mensagem.assunto, mensagem.corpo, como_json=args.json)

    _titulo(f"Corpo ({len(mensagem.corpo)} caracteres)")
    limite = args.corpo if args.corpo else len(mensagem.corpo)
    print(mensagem.corpo[:limite] or "(corpo vazio)")
    if limite < len(mensagem.corpo):
        print(f"\n[... {len(mensagem.corpo) - limite} caracteres omitidos]")

    if args.ia:
        _executar_ia(mensagem)

    return 0


def cmd_testar(args) -> int:
    assunto = args.assunto or ""
    corpo = args.corpo or ""

    if args.arquivo:
        conteudo = Path(args.arquivo).read_text(encoding="utf-8")
        if args.assunto:
            corpo = conteudo
        else:
            linhas = conteudo.splitlines()
            assunto = linhas[0] if linhas else ""
            corpo = "\n".join(linhas[1:])
    elif args.stdin or not (assunto or corpo):
        entrada = sys.stdin.read()
        if not assunto and not corpo:
            linhas = entrada.splitlines()
            assunto = linhas[0] if linhas else ""
            corpo = "\n".join(linhas[1:])
        else:
            corpo = (corpo + "\n" + entrada).strip()

    _titulo("Entrada")
    print(f"Assunto: {assunto or '—'}")
    if corpo:
        recorte = corpo[:400]
        print(f"Corpo  : {recorte}{'...' if len(corpo) > 400 else ''}")

    _titulo("Extração determinística (sem IA)")
    _mostrar_analise(assunto, corpo, como_json=args.json)

    if args.ia:
        from app.modules.google.gmail import MensagemEmail

        momento = __import__("datetime").datetime.now()
        _executar_ia(
            MensagemEmail(
                id="TESTE",
                thread_id="TESTE",
                remetente_nome=args.remetente or "Teste",
                remetente_email="teste@exemplo.com",
                assunto=assunto,
                data_recebimento=momento,
                corpo=corpo,
            )
        )

    return 0


def cmd_banco(args) -> int:
    from sqlalchemy import select

    from app.core.db import SessionLocal, init_db
    from app.modules.prazos.models import Email, Prazo

    init_db()
    db = SessionLocal()

    try:
        consulta = select(Email).order_by(Email.data_recebimento.desc()).limit(args.limite)
        if args.status:
            consulta = consulta.where(Email.status == args.status)

        emails = list(db.scalars(consulta).all())

        if not emails:
            print("Nenhum e-mail no banco.")
            return 0

        _titulo(f"{len(emails)} e-mail(ns) no banco")
        for email in emails:
            prazos = db.scalars(select(Prazo).where(Prazo.email_id == email.id)).all()
            print(
                f"{email.data_recebimento:%d/%m/%Y %H:%M} | {email.status:12s} | "
                f"{email.numero_processo or '—':24s} | {email.assunto[:60]}"
            )
            for prazo in prazos:
                print(
                    f"      ↳ {prazo.tipo_prazo or '—':28s} "
                    f"{prazo.data_prazo or '—'} "
                    f"{prazo.advogado or '—'} "
                    f"[{prazo.status}]"
                )
    finally:
        db.close()

    return 0


def cmd_planilha(args) -> int:
    from app.core.db import SessionLocal, init_db
    from app.modules.prazos.services.espelho_sheets import sincronizar_planilha

    settings = get_settings()
    if not settings.sheets_configurado:
        print(
            "Planilha não configurada.\n"
            "Defina GOOGLE_SERVICE_ACCOUNT_JSON e GOOGLE_SHEET_ID no .env "
            "(e compartilhe a planilha com o e-mail da conta de serviço)."
        )
        return 1

    init_db()
    db = SessionLocal()

    try:
        resultado = sincronizar_planilha(db, settings)
    finally:
        db.close()

    print(json.dumps(resultado.as_dict(), ensure_ascii=False, indent=2))
    return 0 if resultado.ok else 1


def cmd_sincronizar(args) -> int:
    from app.core.db import SessionLocal, init_db
    from app.modules.google.gmail import GmailClient
    from app.modules.prazos.services.deepseek import DeepSeekClient
    from app.modules.prazos.services.ingest import (
        query_continuo,
        query_historico,
        sincronizar,
    )

    settings = get_settings()

    if not settings.gmail_configurado:
        print("Credenciais do Gmail ausentes. Rode: python scripts/authorize_gmail.py")
        return 1
    if not settings.deepseek_configurado:
        print("DEEPSEEK_API_KEY ausente.")
        return 1

    init_db()

    if args.modo == "historico":
        consulta = query_historico(settings)
        checkpoint = True
    else:
        consulta = query_continuo(settings)
        checkpoint = False

    db = SessionLocal()
    try:
        resultado = sincronizar(
            db,
            settings=settings,
            gmail=GmailClient(settings, conta=args.conta),
            deepseek=DeepSeekClient(settings),
            query=consulta,
            limite=args.limite,
            usar_checkpoint=checkpoint,
            deadline_s=args.segundos,
        )
    finally:
        db.close()

    print(json.dumps(resultado.as_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_explorar(args) -> int:
    """Modo interativo para investigar e-mails e testar regras."""
    from app.modules.google.gmail import GmailClient, GmailError

    settings = get_settings()
    cliente = (
        GmailClient(settings, conta=args.conta) if settings.gmail_configurado else None
    )

    if cliente is None:
        print("⚠️  Sem credenciais do Gmail: apenas o modo 'testar' funcionará.")

    _titulo("Explorador de e-mails")
    print("Comandos:")
    print("  l [query]   lista mensagens (padrão: a query do .env)")
    print("  a <id>      abre uma mensagem")
    print("  t           testa um texto colado (Ctrl+D para finalizar)")
    print("  q           sair")

    while True:
        try:
            entrada = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not entrada:
            continue

        comando, _, resto = entrada.partition(" ")
        comando = comando.lower()

        if comando in ("q", "sair", "exit"):
            return 0

        if comando in ("l", "listar"):
            if cliente is None:
                print("Gmail não configurado.")
                continue
            query = resto.strip() or settings.gmail_query
            try:
                pagina = cliente.listar_mensagens(query, max_results=args.limite)
            except GmailError as exc:
                print(f"Erro: {exc}")
                continue

            for message_id in pagina.ids:
                try:
                    mensagem = cliente.obter_mensagem(message_id)
                except GmailError as exc:
                    print(f"  [{message_id}] erro: {exc}")
                    continue
                processos = extrair_numeros_processo(mensagem.assunto)
                print(
                    f"  {mensagem.data_recebimento:%d/%m} "
                    f"{(processos[0] if processos else '—'):26s} "
                    f"{mensagem.assunto[:60]}  ({message_id})"
                )

        elif comando in ("a", "abrir"):
            if cliente is None or not resto.strip():
                print("Uso: a <message_id>")
                continue
            try:
                mensagem = cliente.obter_mensagem(resto.strip())
            except GmailError as exc:
                print(f"Erro: {exc}")
                continue

            print(f"\nAssunto: {mensagem.assunto}")
            print(f"De     : {mensagem.remetente_nome} <{mensagem.remetente_email}>")
            print(f"Data   : {mensagem.data_recebimento:%d/%m/%Y %H:%M} UTC")
            _mostrar_analise(mensagem.assunto, mensagem.corpo)
            print("\n--- corpo ---")
            print(mensagem.corpo[: args.corpo])

        elif comando in ("t", "testar"):
            print("Cole o texto (assunto na 1ª linha). Ctrl+D para finalizar:")
            try:
                texto = sys.stdin.read()
            except KeyboardInterrupt:
                continue
            linhas = texto.splitlines()
            assunto = linhas[0] if linhas else ""
            corpo = "\n".join(linhas[1:])
            _mostrar_analise(assunto, corpo)

        else:
            print("Comando não reconhecido.")


# ---------------------------------------------------------------------------
def _executar_ia(mensagem) -> None:
    from app.modules.prazos.services.deepseek import DeepSeekClient, DeepSeekError
    from app.modules.prazos.services.extracao import extrair_numeros_processo

    settings = get_settings()
    if not settings.deepseek_configurado:
        print("\n(DEEPSEEK_API_KEY ausente — etapa de IA ignorada.)")
        return

    cliente = DeepSeekClient(settings)

    try:
        prazos = cliente.extrair_prazos(
            assunto=mensagem.assunto,
            remetente_nome=mensagem.remetente_nome,
            remetente_email=mensagem.remetente_email,
            data_recebimento=mensagem.data_recebimento.strftime("%d/%m/%Y %H:%M"),
            corpo=mensagem.corpo,
            processos_conhecidos=extrair_numeros_processo(mensagem.assunto),
        )
    except DeepSeekError as exc:
        print(f"\nErro na IA: {exc}")
        return

    _titulo(f"Resultado da IA ({len(prazos)} prazo(s))")
    for prazo in prazos:
        print(
            f"• {prazo.tipo_prazo} | {prazo.data_prazo or '—'} | "
            f"{prazo.numero_processo or '—'} | {prazo.advogado} [{prazo.status}]"
        )
    if not prazos:
        print("Nenhum prazo identificado.")


# ---------------------------------------------------------------------------
def cmd_repasses(args) -> int:
    """Lista as tarefas pendentes da planilha de repasses."""
    from app.modules.repasses.services import dominio
    from app.modules.repasses.services.planilha import (
        RepassesError,
        RepassesPlanilha,
    )

    settings = get_settings()
    if not settings.repasses_configurado:
        print(
            "Planilha de repasses não configurada.\n"
            "Defina GOOGLE_SERVICE_ACCOUNT_JSON e REPASSES_SHEET_ID no .env."
        )
        return 1

    try:
        repasses = RepassesPlanilha(settings).listar()
    except RepassesError as exc:
        print(f"Erro ao ler a planilha: {exc}")
        return 1

    if args.json:
        print(json.dumps([r.as_dict() for r in dominio.pendentes(repasses)], ensure_ascii=False, indent=2))
        return 0

    resumo = dominio.resumir(repasses)
    _titulo(
        f"{resumo.total} repasse(s) · "
        f"{resumo.pendentes + resumo.em_andamento} em aberto · "
        f"{resumo.atrasados} atrasado(s)"
    )

    for repasse in dominio.pendentes(repasses):
        atraso = " ⚠️" if repasse.atrasado() else ""
        print(
            f"{repasse.id:12s} | {repasse.cliente[:28]:28s} | "
            f"{repasse.valor_formatado:>12s} | "
            f"{repasse.data_prevista or '—'} | "
            f"{repasse.status.rotulo}{atraso}"
        )

    print(f"\nValor em aberto: {resumo.valor_aberto_formatado}")
    return 0


def cmd_repasse_token(args) -> int:
    """Gera o link individual de acompanhamento de um cliente."""
    from app.modules.repasses.tokens import TokenClienteError, link_cliente

    settings = get_settings()
    try:
        link = link_cliente(args.cliente, settings.repasses_cliente_token_secret)
    except TokenClienteError as exc:
        print(f"Erro: {exc}")
        return 1

    print(f"{args.base.rstrip('/')}{link}" if args.base else link)
    return 0


# ---------------------------------------------------------------------------
def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prazos",
        description="CLI de exploração da extração de prazos jurídicos",
    )
    parser.add_argument("--json", action="store_true", help="saída em JSON")

    sub = parser.add_subparsers(dest="comando", required=True)

    p_emails = sub.add_parser("emails", help="lista mensagens do Gmail")
    p_emails.add_argument("--query", default=None)
    p_emails.add_argument("--limite", type=int, default=10)
    p_emails.add_argument("--conta", default=None, help="nome da conta Gmail")
    p_emails.set_defaults(func=cmd_emails)

    p_abrir = sub.add_parser("abrir", help="mostra uma mensagem e a extração")
    p_abrir.add_argument("message_id")
    p_abrir.add_argument("--corpo", type=int, default=0, help="limite de caracteres do corpo")
    p_abrir.add_argument("--ia", action="store_true", help="também chama a DeepSeek")
    p_abrir.add_argument("--conta", default=None, help="nome da conta Gmail")
    p_abrir.set_defaults(func=cmd_abrir)

    p_testar = sub.add_parser("testar", help="testa as regras num texto")
    p_testar.add_argument("--assunto", default="")
    p_testar.add_argument("--corpo", default="")
    p_testar.add_argument("--arquivo", default=None)
    p_testar.add_argument("--stdin", action="store_true")
    p_testar.add_argument("--remetente", default=None)
    p_testar.add_argument("--ia", action="store_true", help="também chama a DeepSeek")
    p_testar.set_defaults(func=cmd_testar)

    p_banco = sub.add_parser("banco", help="lista o que já foi processado")
    p_banco.add_argument("--status", default=None)
    p_banco.add_argument("--limite", type=int, default=20)
    p_banco.set_defaults(func=cmd_banco)

    p_planilha = sub.add_parser("planilha", help="espelha os prazos no Google Sheets")
    p_planilha.set_defaults(func=cmd_planilha)

    p_sync = sub.add_parser("sincronizar", help="sincroniza e-mails do Gmail")
    p_sync.add_argument("--modo", choices=["continuo", "historico"], default="continuo")
    p_sync.add_argument("--limite", type=int, default=None)
    p_sync.add_argument("--segundos", type=float, default=None)
    p_sync.add_argument("--conta", default=None, help="nome da conta Gmail")
    p_sync.set_defaults(func=cmd_sincronizar)

    p_repasses = sub.add_parser("repasses", help="lista as tarefas pendentes de repasses")
    p_repasses.set_defaults(func=cmd_repasses)

    p_token = sub.add_parser(
        "repasse-token", help="gera o link de acompanhamento de um cliente"
    )
    p_token.add_argument("cliente")
    p_token.add_argument("--base", default="", help="URL base (ex.: https://app.vercel.app)")
    p_token.set_defaults(func=cmd_repasse_token)

    p_exp = sub.add_parser("explorar", help="modo interativo")
    p_exp.add_argument("--limite", type=int, default=10)
    p_exp.add_argument("--corpo", type=int, default=2500)
    p_exp.add_argument("--conta", default=None, help="nome da conta Gmail")
    p_exp.set_defaults(func=cmd_explorar)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = montar_parser()
    args = parser.parse_args(argv)
    args.json = getattr(args, "json", False)

    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
