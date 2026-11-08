"""Configuração do Jinja2 (templates e filtros de formatação)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.modulos import listar_modulos

APP_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

#: Templates compartilhados + templates de cada módulo.
TEMPLATES_DIRS: tuple[Path, ...] = (
    TEMPLATES_DIR,
    APP_DIR / "modules" / "prazos" / "templates",
    APP_DIR / "modules" / "repasses" / "templates",
    APP_DIR / "modules" / "google" / "templates",
)

templates = Jinja2Templates(directory=[str(caminho) for caminho in TEMPLATES_DIRS])


def _formatar_data(valor: date | datetime | None, formato: str = "%d/%m/%Y") -> str:
    if valor is None:
        return "—"
    return valor.strftime(formato)


def _formatar_datahora(valor: datetime | None) -> str:
    """Converte UTC -> fuso configurado e formata."""
    if valor is None:
        return "—"
    from app.modules.prazos.services.reports import para_local

    local = para_local(valor, get_settings().timezone)
    return local.strftime("%d/%m/%Y %H:%M") if local else "—"


templates.env.filters["data"] = _formatar_data
templates.env.filters["datahora"] = _formatar_datahora


def contexto_base(**extra) -> dict:
    """Contexto comum a todas as páginas."""
    settings = get_settings()
    base = {
        "app_name": settings.app_name,
        "timezone": settings.timezone,
        "modulos": listar_modulos(settings),
    }
    base.update(extra)
    return base
