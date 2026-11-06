#!/usr/bin/env python3
"""Executa uma sincronização pelo terminal (útil para cron externo ou debug).

Uso:
    python scripts/sync_cli.py --modo historico --limite 100
    python scripts/sync_cli.py --modo continuo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services.deepseek import DeepSeekClient  # noqa: E402
from app.services.gmail import GmailClient  # noqa: E402
from app.services.ingest import (  # noqa: E402
    query_continuo,
    query_historico,
    sincronizar,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza e-mails de protocolo")
    parser.add_argument("--modo", choices=["continuo", "historico"], default="continuo")
    parser.add_argument("--limite", type=int, default=None, help="Máximo de mensagens")
    parser.add_argument(
        "--segundos", type=float, default=None, help="Orçamento de tempo da execução"
    )
    args = parser.parse_args()

    settings = get_settings()

    if not settings.gmail_configurado:
        print("Credenciais do Gmail ausentes. Rode scripts/authorize_gmail.py.", file=sys.stderr)
        return 1
    if not settings.deepseek_configurado:
        print("DEEPSEEK_API_KEY ausente.", file=sys.stderr)
        return 1

    init_db()

    if args.modo == "historico":
        query = query_historico(settings)
        usar_checkpoint = True
    else:
        query = query_continuo(settings)
        usar_checkpoint = False

    db = SessionLocal()
    try:
        resultado = sincronizar(
            db,
            settings=settings,
            gmail=GmailClient(settings),
            deepseek=DeepSeekClient(settings),
            query=query,
            limite=args.limite,
            usar_checkpoint=usar_checkpoint,
            deadline_s=args.segundos,
        )
    finally:
        db.close()

    print(json.dumps(resultado.as_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
