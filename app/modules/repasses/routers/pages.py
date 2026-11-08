"""Páginas HTML do módulo de repasses."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import RedirectResponse

from app.core.config import Settings
from app.core.deps import settings_dep
from app.core.templating import contexto_base, templates
from app.modules.repasses.deps import get_planilha
from app.modules.repasses.modelos import StatusRepasse
from app.modules.repasses.services import dominio
from app.modules.repasses.services.planilha import RepassesError, RepassesPlanilha
from app.modules.repasses.tokens import link_cliente, validar_token_cliente

router = APIRouter(prefix="/repasses", tags=["repasses"])


@router.get("", summary="Painel de repasses")
def dashboard(
    request: Request,
    status: str | None = None,
    cliente: str | None = None,
    q: str | None = None,
    settings: Settings = Depends(settings_dep),
    planilha: RepassesPlanilha = Depends(get_planilha),
):
    erro: str | None = None
    repasses = []

    if not settings.repasses_configurado:
        erro = (
            "Planilha de repasses não configurada. Defina "
            "GOOGLE_SERVICE_ACCOUNT_JSON e REPASSES_SHEET_ID."
        )
    else:
        try:
            repasses = planilha.listar()
        except RepassesError as exc:
            erro = str(exc)

    filtrados = dominio.filtrar(
        repasses, status=status, cliente=cliente, texto=q
    )
    tarefas = dominio.pendentes(filtrados)
    resumos = dominio.resumos_por_cliente(repasses)

    links: dict[str, str] = {}
    if settings.repasses_cliente_token_secret:
        links = {
            resumo.cliente: link_cliente(
                resumo.cliente, settings.repasses_cliente_token_secret
            )
            for resumo in resumos
        }

    return templates.TemplateResponse(
        request=request,
        name="repasses_dashboard.html",
        context=contexto_base(
            titulo="Repasses",
            rota_ativa="repasses",
            erro=erro,
            resumo=dominio.resumir(repasses),
            tarefas=tarefas,
            atrasados=dominio.atrasados(repasses),
            clientes_resumo=resumos,
            links_clientes=links,
            repasses=dominio.ordenar_pendentes(filtrados),
            filtros={
                "status": status or "",
                "cliente": cliente or "",
                "q": q or "",
            },
            status_opcoes=list(StatusRepasse),
            segredo_configurado=bool(settings.repasses_cliente_token_secret),
        ),
    )


@router.get("/cliente/{cliente}", summary="Status público de um cliente")
def pagina_cliente(
    request: Request,
    cliente: str,
    token: str | None = None,
    settings: Settings = Depends(settings_dep),
    planilha: RepassesPlanilha = Depends(get_planilha),
):
    segredo = settings.repasses_cliente_token_secret

    if not segredo:
        return _acesso_negado(
            request,
            mensagem=(
                "Os links de acompanhamento ainda não estão habilitados. "
                "Configure REPASSES_CLIENTE_TOKEN_SECRET."
            ),
            codigo=503,
        )

    if not validar_token_cliente(cliente, token, segredo):
        return _acesso_negado(
            request,
            mensagem="Este link é inválido ou expirou. Solicite um novo link.",
            codigo=403,
        )

    try:
        resumo = dominio.resumo_cliente(planilha.listar(), cliente)
    except RepassesError as exc:
        return _acesso_negado(request, mensagem=str(exc), codigo=503)

    return templates.TemplateResponse(
        request=request,
        name="repasses_cliente.html",
        context={
            "titulo": f"Repasses · {resumo.cliente}",
            "app_name": settings.app_name,
            "timezone": settings.timezone,
            "resumo_cliente": resumo,
        },
    )


@router.post("/{repasse_id}/status", summary="Atualiza o status de um repasse")
def atualizar_status(
    repasse_id: str,
    status: str = Form(...),
    data_pagamento: str = Form(default=""),
    observacoes: str = Form(default=""),
    planilha: RepassesPlanilha = Depends(get_planilha),
) -> RedirectResponse:
    campos: dict[str, object] = {"status": status}
    if data_pagamento.strip():
        campos["data_pagamento"] = data_pagamento.strip()
    if observacoes.strip():
        campos["observacoes"] = observacoes.strip()

    try:
        planilha.atualizar(repasse_id, campos)
    except RepassesError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return RedirectResponse(
        url="/repasses", status_code=http_status.HTTP_303_SEE_OTHER
    )


def _acesso_negado(request: Request, *, mensagem: str, codigo: int):
    return templates.TemplateResponse(
        request=request,
        name="repasses_acesso_negado.html",
        context={
            "titulo": "Acesso negado",
            "app_name": "Repasses",
            "mensagem": mensagem,
        },
        status_code=codigo,
    )
