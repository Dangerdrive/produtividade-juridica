"""Dependências compartilhadas do FastAPI."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Query, Request, status

from app.config import Settings, get_settings


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
