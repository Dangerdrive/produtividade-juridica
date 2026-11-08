"""API JSON do módulo de repasses."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.deps import exigir_token_repasses, settings_dep
from app.modules.repasses.deps import get_planilha
from app.modules.repasses.modelos import StatusRepasse
from app.modules.repasses.services import dominio
from app.modules.repasses.services.planilha import RepassesError, RepassesPlanilha
from app.modules.repasses.tokens import validar_token_cliente

router = APIRouter(prefix="/api/repasses", tags=["repasses"])


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------
class RepasseCreate(BaseModel):
    id: str | None = None
    cliente: str
    processo: str = ""
    valor: Decimal | None = None
    data_prevista: date | None = None
    data_pagamento: date | None = None
    status: StatusRepasse = StatusRepasse.PENDENTE
    responsavel: str = ""
    observacoes: str = ""


class RepasseUpdate(BaseModel):
    cliente: str | None = Field(default=None)
    processo: str | None = Field(default=None)
    valor: Decimal | None = Field(default=None)
    data_prevista: date | None = Field(default=None)
    data_pagamento: date | None = Field(default=None)
    status: StatusRepasse | None = Field(default=None)
    responsavel: str | None = Field(default=None)
    observacoes: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------
def _erro(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
    )


# ---------------------------------------------------------------------------
# Leitura interna
# ---------------------------------------------------------------------------
@router.get(
    "",
    summary="Lista repasses",
    dependencies=[Depends(exigir_token_repasses)],
)
def listar(
    status: str | None = None,
    cliente: str | None = None,
    pendentes: bool = False,
    q: str | None = None,
    planilha: RepassesPlanilha = Depends(get_planilha),
) -> dict:
    try:
        itens = dominio.filtrar(
            planilha.listar(),
            status=status,
            cliente=cliente,
            apenas_pendentes=pendentes,
            texto=q,
        )
    except RepassesError as exc:
        raise _erro(exc) from exc

    itens = dominio.ordenar_pendentes(itens)
    return {
        "total": len(itens),
        "resumo": dominio.resumir(itens).as_dict(),
        "itens": [repasse.as_dict() for repasse in itens],
    }


@router.get(
    "/pendentes",
    summary="Tarefas pendentes de repasse",
    dependencies=[Depends(exigir_token_repasses)],
)
def tarefas_pendentes(
    planilha: RepassesPlanilha = Depends(get_planilha),
) -> dict:
    try:
        itens = dominio.pendentes(planilha.listar())
    except RepassesError as exc:
        raise _erro(exc) from exc

    return {
        "total": len(itens),
        "resumo": dominio.resumir(itens).as_dict(),
        "itens": [repasse.as_dict() for repasse in itens],
    }


@router.get(
    "/clientes",
    summary="Resumo por cliente",
    dependencies=[Depends(exigir_token_repasses)],
)
def clientes(planilha: RepassesPlanilha = Depends(get_planilha)) -> dict:
    try:
        resumos = dominio.resumos_por_cliente(planilha.listar())
    except RepassesError as exc:
        raise _erro(exc) from exc

    return {
        "total": len(resumos),
        "clientes": [
            {
                "cliente": resumo.cliente,
                "resumo": resumo.resumo.as_dict(),
                "proxima_data_prevista": resumo.proxima_data_prevista.isoformat()
                if resumo.proxima_data_prevista
                else None,
            }
            for resumo in resumos
        ],
    }


# ---------------------------------------------------------------------------
# Visão do cliente (token individual)
# ---------------------------------------------------------------------------
@router.get("/cliente/{cliente}", summary="Status público de um cliente")
def status_cliente(
    cliente: str,
    token: str | None = Query(default=None),
    settings: Settings = Depends(settings_dep),
    planilha: RepassesPlanilha = Depends(get_planilha),
) -> dict:
    segredo = settings.repasses_cliente_token_secret
    if not segredo:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Links de cliente desabilitados: configure "
                "REPASSES_CLIENTE_TOKEN_SECRET."
            ),
        )

    if not validar_token_cliente(cliente, token, segredo):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Link inválido ou expirado.",
        )

    try:
        resumo = dominio.resumo_cliente(planilha.listar(), cliente)
    except RepassesError as exc:
        raise _erro(exc) from exc

    return resumo.as_dict()


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------
@router.get(
    "/{repasse_id}",
    summary="Obtém um repasse",
    dependencies=[Depends(exigir_token_repasses)],
)
def obter(repasse_id: str, planilha: RepassesPlanilha = Depends(get_planilha)) -> dict:
    try:
        repasse = planilha.obter(repasse_id)
    except RepassesError as exc:
        raise _erro(exc) from exc

    if repasse is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repasse {repasse_id} não encontrado.",
        )
    return repasse.as_dict()


@router.post(
    "",
    summary="Cria um repasse na planilha",
    status_code=http_status.HTTP_201_CREATED,
    dependencies=[Depends(exigir_token_repasses)],
)
def criar(
    payload: RepasseCreate, planilha: RepassesPlanilha = Depends(get_planilha)
) -> dict:
    try:
        repasse = planilha.criar(payload.model_dump(exclude_none=True))
    except RepassesError as exc:
        raise _erro(exc) from exc

    return repasse.as_dict()


@router.patch(
    "/{repasse_id}",
    summary="Atualiza um repasse na planilha",
    dependencies=[Depends(exigir_token_repasses)],
)
def atualizar(
    repasse_id: str,
    payload: RepasseUpdate,
    planilha: RepassesPlanilha = Depends(get_planilha),
) -> dict:
    campos = payload.model_dump(exclude_unset=True)

    try:
        repasse = planilha.atualizar(repasse_id, campos)
    except RepassesError as exc:
        raise _erro(exc) from exc

    return repasse.as_dict()
