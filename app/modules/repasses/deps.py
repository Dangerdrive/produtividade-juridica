"""Dependências do módulo de repasses."""

from __future__ import annotations

from fastapi import Depends

from app.core.config import Settings
from app.core.deps import settings_dep
from app.modules.repasses.services.planilha import RepassesPlanilha


def get_planilha(settings: Settings = Depends(settings_dep)) -> RepassesPlanilha:
    """Entrega o repositório de repasses configurado para a requisição."""
    return RepassesPlanilha(settings)
