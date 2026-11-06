"""Rotas de sincronização e monitoramento."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.deps import exigir_token, settings_dep
from app.models import Email, Prazo
from app.services.deepseek import DeepSeekClient
from app.services.gmail import GmailClient
from app.services.ingest import query_continuo, query_historico, sincronizar

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sincronização"])


@router.get("/health", summary="Estado da aplicação")
def health(db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)) -> dict:
    """Verifica o banco e se as credenciais estão presentes."""
    try:
        db.execute(text("SELECT 1"))
        banco_ok = True
    except Exception:  # pragma: no cover - depende do ambiente
        banco_ok = False

    return {
        "status": "ok" if banco_ok else "degradado",
        "banco": banco_ok,
        "gmail_configurado": settings.gmail_configurado,
        "deepseek_configurado": settings.deepseek_configurado,
        "planilha_configurada": settings.sheets_configurado,
        "sincronizacao_protegida": bool(settings.sync_token),
        "emails": db.scalar(select(func.count(Email.id))) or 0,
        "prazos": db.scalar(select(func.count(Prazo.id))) or 0,
        "prazos_na_planilha": db.scalar(
            select(func.count(Prazo.id)).where(Prazo.sheet_linha.is_not(None))
        )
        or 0,
    }


@router.post(
    "/api/sync",
    summary="Executa a sincronização manualmente",
    dependencies=[Depends(exigir_token)],
)
def sincronizar_manual(
    request: Request,
    modo: str = Query("continuo", pattern="^(continuo|historico)$"),
    limite: int | None = Query(default=None, ge=1, le=500),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Sincroniza e-mails.

    - ``modo=continuo``: apenas não lidos da caixa de entrada (marca como lido).
    - ``modo=historico``: janela retroativa com checkpoint de paginação.
    """
    return _executar(db, settings, modo=modo, limite=limite)


@router.get(
    "/api/cron/sync",
    summary="Endpoint para Cron Jobs da Vercel",
    dependencies=[Depends(exigir_token)],
)
def sincronizar_cron(
    request: Request,
    modo: str = Query("continuo", pattern="^(continuo|historico)$"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Mesma lógica do `/api/sync`, exposta em GET para o agendador."""
    return _executar(db, settings, modo=modo, limite=None)


@router.post(
    "/api/sync/sheets",
    summary="Espelha os prazos na planilha do Google Sheets",
    dependencies=[Depends(exigir_token)],
)
def espelhar_planilha(
    limite: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Envía para a aba ``DB_Prazos`` os prazos novos e os alterados.

    Idempotente: a coluna A guarda o ID do prazo, então reexecutar não duplica.
    """
    from app.services.sheets import sincronizar_planilha

    if not settings.sheets_configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Planilha não configurada "
            "(GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_ID).",
        )

    resultado = sincronizar_planilha(db, settings, limite=limite)

    if not resultado.ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=resultado.erro
        )

    return resultado.as_dict()


def _executar(
    db: Session,
    settings: Settings,
    *,
    modo: str,
    limite: int | None,
) -> dict:
    if not settings.gmail_configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credenciais do Gmail não configuradas.",
        )
    if not settings.deepseek_configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEEPSEEK_API_KEY não configurada.",
        )

    gmail = GmailClient(settings)
    deepseek = DeepSeekClient(settings)

    if modo == "historico":
        query = query_historico(settings)
        usar_checkpoint = True
    else:
        query = query_continuo(settings)
        usar_checkpoint = False

    try:
        resultado = sincronizar(
            db,
            settings=settings,
            gmail=gmail,
            deepseek=deepseek,
            query=query,
            limite=limite,
            usar_checkpoint=usar_checkpoint,
        )
    except Exception as exc:  # pragma: no cover - salvaguarda
        logger.exception("Falha na sincronização")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha na sincronização: {exc}",
        ) from exc

    logger.info("Sincronização concluída: %s", resultado.as_dict())
    return {"modo": modo, **resultado.as_dict()}
