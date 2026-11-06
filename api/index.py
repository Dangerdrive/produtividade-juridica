"""Entrypoint da Vercel.

A Vercel procura um atributo ``app`` (ASGI) neste arquivo. O ``sys.path`` é
ajustado para permitir ``import app.*`` a partir da raiz do projeto.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.main import app  # noqa: E402

__all__ = ["app"]
