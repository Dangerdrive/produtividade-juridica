#!/usr/bin/env python3
"""Cria o schema do banco (e, opcionalmente, dados de demonstração).

Uso:
    python scripts/init_db.py
    python scripts/init_db.py --demo
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal, init_db  # noqa: E402
from app.modules.prazos.models import Email, Prazo, StatusExtracao  # noqa: E402

DEMO = [
    {
        "message_id": "demo-1",
        "remetente": ("Secretaria TJSP", "protocolo@tjsp.jus.br"),
        "assunto": "Intimação para manifestação",
        "dias_atras": 1,
        "prazos": [
            ("Dra. Maria Souza", "Manifestação", 20, "0001234-56.2024.8.26.0100"),
        ],
    },
    {
        "message_id": "demo-2",
        "remetente": ("Protocolo TRT-2", "protocolo@trt2.jus.br"),
        "assunto": "Publicação de acórdão — dois processos",
        "dias_atras": 3,
        "prazos": [
            ("Dr. João Lima", "Recurso de Revista", 25, "0002222-33.2024.5.02.0001"),
            ("Dr. João Lima", "Contrarrazões", 25, "0003333-44.2024.5.02.0002"),
        ],
    },
    {
        "message_id": "demo-3",
        "remetente": ("Newsletter Jurídica", "news@exemplo.com"),
        "assunto": "Informativo semanal",
        "dias_atras": 5,
        "prazos": [],
    },
]


def criar_demo() -> int:
    db = SessionLocal()
    criados = 0
    try:
        hoje = datetime.now(UTC).replace(tzinfo=None)

        for item in DEMO:
            if db.query(Email).filter_by(gmail_message_id=item["message_id"]).first():
                continue

            nome, email = item["remetente"]
            registro = Email(
                gmail_message_id=item["message_id"],
                gmail_thread_id=item["message_id"],
                remetente_nome=nome,
                remetente_email=email,
                assunto=item["assunto"],
                data_recebimento=hoje - timedelta(days=item["dias_atras"]),
                status=StatusExtracao.OK.value
                if item["prazos"]
                else StatusExtracao.SEM_PRAZO.value,
                processado_em=hoje,
            )

            for advogado, tipo, em_dias, processo in item["prazos"]:
                registro.prazos.append(
                    Prazo(
                        advogado=advogado,
                        tipo_prazo=tipo,
                        data_prazo=date.today() + timedelta(days=em_dias),
                        numero_processo=processo,
                        status=StatusExtracao.OK.value,
                    )
                )

            db.add(registro)
            criados += 1

        db.commit()
    finally:
        db.close()

    return criados


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicializa o banco")
    parser.add_argument("--demo", action="store_true", help="Insere dados de demonstração")
    args = parser.parse_args()

    settings = get_settings()
    print(f"Banco: {settings.database_url}")

    init_db()
    print("Schema criado/verificado.")

    if args.demo:
        quantidade = criar_demo()
        print(f"{quantidade} e-mail(ns) de demonstração inserido(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
