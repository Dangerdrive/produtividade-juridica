#!/usr/bin/env python3
"""Gera links individuais de acompanhamento para clientes de repasses.

Uso:
    python scripts/token_repasses.py --cliente "Maria Souza"
    python scripts/token_repasses.py --cliente "Maria Souza" --base https://app.vercel.app
    python scripts/token_repasses.py --todos
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.core.config import get_settings  # noqa: E402
from app.modules.repasses.services.planilha import (  # noqa: E402
    RepassesError,
    RepassesPlanilha,
)
from app.modules.repasses.tokens import TokenClienteError, link_cliente  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera links de acompanhamento por cliente"
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--cliente", help="nome do cliente")
    grupo.add_argument("--todos", action="store_true", help="gera para todos os clientes")
    parser.add_argument("--base", default="", help="URL base (ex.: https://app.vercel.app)")
    args = parser.parse_args()

    settings = get_settings()

    if not settings.repasses_cliente_token_secret:
        print(
            "REPASSES_CLIENTE_TOKEN_SECRET não configurado no .env.",
            file=sys.stderr,
        )
        return 1

    base = args.base.rstrip("/")

    try:
        if args.todos:
            if not settings.repasses_configurado:
                print(
                    "Planilha de repasses não configurada "
                    "(GOOGLE_SERVICE_ACCOUNT_JSON / REPASSES_SHEET_ID).",
                    file=sys.stderr,
                )
                return 1

            repasses = RepassesPlanilha(settings).listar()
            clientes = sorted({r.cliente for r in repasses}, key=str.casefold)
            if not clientes:
                print("Nenhum cliente encontrado na planilha.")
                return 0

            for cliente in clientes:
                print(f"{cliente}\t{base}{link_cliente(cliente, settings.repasses_cliente_token_secret)}")
            return 0

        print(base + link_cliente(args.cliente, settings.repasses_cliente_token_secret))
    except (TokenClienteError, RepassesError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
