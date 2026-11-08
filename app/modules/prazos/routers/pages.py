"""Rotas de páginas (dashboard e listagem)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.templating import contexto_base, templates
from app.modules.prazos.services import reports

router = APIRouter(prefix="/prazos", tags=["prazos"])


def _parse_data(valor: str | None) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


@router.get("", summary="Dashboard de produtividade")
def dashboard(
    request: Request,
    inicio: str | None = Query(default=None, description="Data inicial (AAAA-MM-DD)"),
    fim: str | None = Query(default=None, description="Data final (AAAA-MM-DD)"),
    db: Session = Depends(get_db),
):
    """Dashboard: matriz Advogado × Tipo de Prazo + indicadores."""
    dt_inicio = _parse_data(inicio)
    dt_fim = _parse_data(fim)

    matriz = reports.matriz_advogado_tipo(db, inicio=dt_inicio, fim=dt_fim)
    resumo = reports.resumo(db)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=contexto_base(
            titulo="Dashboard",
            rota_ativa="prazos",
            resumo=resumo,
            matriz=matriz,
            tipos=reports.distribuicao_por_tipo(db, inicio=dt_inicio, fim=dt_fim),
            status_email=reports.distribuicao_por_status(db),
            por_mes=reports.prazos_por_mes(db),
            proximos=reports.proximos_prazos(db),
            vencidos=reports.prazos_vencidos(db, limite=10),
            filtro_inicio=inicio or "",
            filtro_fim=fim or "",
        ),
    )


@router.get("/lista", summary="Listagem de prazos")
def listar_prazos(
    request: Request,
    advogado: str | None = None,
    tipo: str | None = None,
    status: str | None = None,
    inicio: str | None = None,
    fim: str | None = None,
    q: str | None = None,
    pagina: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tabela paginada com filtros por advogado, tipo, status e período."""
    resultado = reports.consultar_prazos(
        db,
        advogado=advogado,
        tipo=tipo,
        status=status,
        inicio=_parse_data(inicio),
        fim=_parse_data(fim),
        texto=q,
        pagina=pagina,
    )

    matriz = reports.matriz_advogado_tipo(db)

    return templates.TemplateResponse(
        request=request,
        name="prazos.html",
        context=contexto_base(
            titulo="Prazos",
            rota_ativa="prazos",
            pagina_prazos=resultado,
            advogados=matriz.advogados,
            tipos=matriz.tipos,
            filtros={
                "advogado": advogado or "",
                "tipo": tipo or "",
                "status": status or "",
                "inicio": inicio or "",
                "fim": fim or "",
                "q": q or "",
            },
        ),
    )
