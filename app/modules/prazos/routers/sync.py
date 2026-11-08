"""Rotas de sincronização e monitoramento."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import get_db
from app.core.deps import exigir_token, settings_dep
from app.modules.google.gmail import GmailClient
from app.modules.prazos.services.deepseek import DeepSeekClient
from app.modules.prazos.services.ingest import query_continuo, query_historico, sincronizar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prazos", tags=["prazos"])


@router.post(
    "/sync",
    summary="Executa a sincronização manualmente",
    dependencies=[Depends(exigir_token)],
)
def sincronizar_manual(
    request: Request,
    modo: str = Query("continuo", pattern="^(continuo|historico)$"),
    limite: int | None = Query(default=None, ge=1, le=500),
    conta: str | None = Query(default=None, description="Nome da conta Gmail"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Sincroniza e-mails.

    - ``modo=continuo``: apenas não lidos da caixa de entrada (marca como lido).
    - ``modo=historico``: janela retroativa com checkpoint de paginação.
    """
    return _executar(db, settings, modo=modo, limite=limite, conta=conta)


@router.get(
    "/cron/sync",
    summary="Endpoint para Cron Jobs da Vercel",
    dependencies=[Depends(exigir_token)],
)
def sincronizar_cron(
    request: Request,
    modo: str = Query("continuo", pattern="^(continuo|historico)$"),
    conta: str | None = Query(default=None, description="Nome da conta Gmail"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Mesma lógica de ``/api/prazos/sync``, exposta em GET para o agendador."""
    return _executar(db, settings, modo=modo, limite=None, conta=conta)


@router.post(
    "/sync/sheets",
    summary="Espelha os prazos na planilha do Google Sheets",
    dependencies=[Depends(exigir_token)],
)
def espelhar_planilha(
    limite: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Envia para a aba ``DB_Prazos`` os prazos novos e os alterados.

    Idempotente: a coluna A guarda o ID do prazo, então reexecutar não duplica.
    """
    from app.modules.prazos.services.espelho_sheets import sincronizar_planilha

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
    conta: str | None = None,
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

    gmail = GmailClient(settings, conta=conta)
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
    return {"modo": modo, "conta": gmail.nome_conta, **resultado.as_dict()}
