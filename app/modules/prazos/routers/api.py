"""API JSON (consumível por outros sistemas)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.prazos.models import Prazo
from app.modules.prazos.services import reports

router = APIRouter(prefix="/api/prazos", tags=["prazos"])


def _parse_data(valor: str | None) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _serializar(prazo: Prazo) -> dict:
    email = prazo.email
    return {
        "id": prazo.id,
        "advogado": prazo.advogado,
        "tipo_prazo": prazo.tipo_prazo,
        "data_prazo": prazo.data_prazo.isoformat() if prazo.data_prazo else None,
        "numero_processo": prazo.numero_processo,
        "status": prazo.status,
        "email": {
            "id": email.id,
            "gmail_message_id": email.gmail_message_id,
            "conta_gmail": email.conta_gmail,
            "assunto": email.assunto,
            "remetente_nome": email.remetente_nome,
            "remetente_email": email.remetente_email,
            "data_recebimento": email.data_recebimento.isoformat(),
            "status": email.status,
        }
        if email
        else None,
    }


@router.get("/stats", summary="Agregações para gráficos")
def stats(
    inicio: str | None = None,
    fim: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Resumo, matriz Advogado × Tipo e distribuições."""
    dt_inicio = _parse_data(inicio)
    dt_fim = _parse_data(fim)

    matriz = reports.matriz_advogado_tipo(db, inicio=dt_inicio, fim=dt_fim)
    resumo = reports.resumo(db)

    return {
        "resumo": {
            "total_emails": resumo.total_emails,
            "total_prazos": resumo.total_prazos,
            "processos_distintos": resumo.processos_distintos,
            "emails_pendentes": resumo.emails_pendentes,
            "emails_com_erro": resumo.emails_com_erro,
            "prazos_sem_data": resumo.prazos_sem_data,
            "ultima_sincronizacao": resumo.ultima_sincronizacao.isoformat()
            if resumo.ultima_sincronizacao
            else None,
        },
        "matriz": {
            "advogados": matriz.advogados,
            "tipos": matriz.tipos,
            "valores": [
                {"advogado": a, "tipo": t, "quantidade": matriz.valor(a, t)}
                for a in matriz.advogados
                for t in matriz.tipos
            ],
            "total": matriz.total,
        },
        "por_tipo": [
            {"tipo": tipo, "quantidade": qtd}
            for tipo, qtd in reports.distribuicao_por_tipo(db, inicio=dt_inicio, fim=dt_fim)
        ],
        "por_status_email": [
            {"status": status, "quantidade": qtd}
            for status, qtd in reports.distribuicao_por_status(db)
        ],
        "por_mes": [{"mes": mes, "quantidade": qtd} for mes, qtd in reports.prazos_por_mes(db)],
    }


@router.get("", summary="Lista de prazos em JSON")
def listar(
    advogado: str | None = None,
    tipo: str | None = None,
    status: str | None = None,
    inicio: str | None = None,
    fim: str | None = None,
    q: str | None = None,
    pagina: int = Query(default=1, ge=1),
    por_pagina: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    resultado = reports.consultar_prazos(
        db,
        advogado=advogado,
        tipo=tipo,
        status=status,
        inicio=_parse_data(inicio),
        fim=_parse_data(fim),
        texto=q,
        pagina=pagina,
        por_pagina=por_pagina,
    )

    return {
        "total": resultado.total,
        "pagina": resultado.pagina,
        "por_pagina": resultado.por_pagina,
        "total_paginas": resultado.total_paginas,
        "itens": [_serializar(prazo) for prazo in resultado.itens],
    }
