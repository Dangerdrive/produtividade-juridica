"""Rotas transversais da plataforma (início e saúde)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import get_db
from app.core.deps import settings_dep
from app.core.templating import contexto_base, templates

router = APIRouter(tags=["plataforma"])


@router.get("/", summary="Página inicial com os módulos")
def home(request: Request):
    """Vitrine dos módulos disponíveis."""
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context=contexto_base(titulo="Módulos", rota_ativa="home"),
    )


@router.get("/health", summary="Estado da aplicação")
def health(
    db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> dict:
    """Verifica o banco e o estado de configuração de cada módulo."""
    from app.modules.prazos.models import Email, Prazo

    try:
        db.execute(text("SELECT 1"))
        banco_ok = True
    except Exception:  # pragma: no cover - depende do ambiente
        banco_ok = False

    return {
        "status": "ok" if banco_ok else "degradado",
        "banco": banco_ok,
        "modulos": {
            "prazos": {
                "gmail_configurado": settings.gmail_configurado,
                "deepseek_configurado": settings.deepseek_configurado,
                "planilha_configurada": settings.sheets_configurado,
                "emails": db.scalar(select(func.count(Email.id))) or 0,
                "prazos": db.scalar(select(func.count(Prazo.id))) or 0,
                "prazos_na_planilha": db.scalar(
                    select(func.count(Prazo.id)).where(
                        Prazo.sheet_linha.is_not(None)
                    )
                )
                or 0,
            },
            "repasses": {
                "planilha_configurada": settings.repasses_configurado,
                "links_cliente_configurados": bool(
                    settings.repasses_cliente_token_secret
                ),
            },
            "google": {
                "conta_servico_configurada": settings.google_configurado,
            },
        },
        # Campos achatados mantidos por compatibilidade com integrações antigas.
        "gmail_configurado": settings.gmail_configurado,
        "deepseek_configurado": settings.deepseek_configurado,
        "planilha_configurada": settings.sheets_configurado,
        "sincronizacao_protegida": bool(settings.sync_token),
    }
