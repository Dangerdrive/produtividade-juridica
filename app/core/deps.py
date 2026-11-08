"""Dependências compartilhadas do FastAPI."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Query, Request, status

from app.core.config import Settings, get_settings


def settings_dep() -> Settings:
    """Injeta a configuração."""
    return get_settings()


def exigir_token(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Protege endpoints de sincronização.

    Aceita três formas:

    * ``Authorization: Bearer <SYNC_TOKEN>`` (uso manual / Vercel Cron)
    * ``?token=<SYNC_TOKEN>`` (agendadores externos simples)
    * cabeçalho ``x-vercel-cron`` (disparo nativo de Cron Jobs da Vercel)
    """
    settings = get_settings()

    if not settings.sync_token:
        # Sem token configurado, só aceitamos o disparo do cron da Vercel.
        if request.headers.get("x-vercel-cron"):
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SYNC_TOKEN não configurado: a sincronização está desabilitada.",
        )

    if (
        authorization
        and authorization.startswith("Bearer ")
        and secrets.compare_digest(authorization[7:].strip(), settings.sync_token)
    ):
        return

    if token and secrets.compare_digest(token, settings.sync_token):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token de sincronização inválido.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def exigir_token_repasses(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Protege a API interna de repasses.

    Usa ``REPASSES_API_TOKEN`` e, se este não estiver definido, cai para
    ``SYNC_TOKEN``. Aceita ``Authorization: Bearer`` ou ``?token=``.
    """
    settings = get_settings()
    esperado = settings.repasses_api_token or settings.sync_token

    if not esperado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "REPASSES_API_TOKEN não configurado: "
                "a API de repasses está desabilitada."
            ),
        )

    if (
        authorization
        and authorization.startswith("Bearer ")
        and secrets.compare_digest(authorization[7:].strip(), esperado)
    ):
        return

    if token and secrets.compare_digest(token, esperado):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token da API de repasses inválido.",
        headers={"WWW-Authenticate": "Bearer"},
    )
