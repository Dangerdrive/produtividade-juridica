"""Aplicação FastAPI.

Estrutura pensada para deploy serverless na Vercel:

* ASGI puro (sem estado em memória além de caches curtos);
* rotas síncronas -> executadas no threadpool, compatíveis com o cliente
  HTTP síncrono e com o SQLAlchemy;
* templates e estáticos empacotados pela função (ver ``vercel.json``).
"""

from __future__ import annotations

import base64
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.routers import api as api_router
from app.routers import pages as pages_router
from app.routers import sync as sync_router
from app.templating import STATIC_DIR

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Garante que o schema exista na primeira inicialização."""
    try:
        init_db()
    except Exception as exc:  # pragma: no cover - ambiente sem banco acessível
        logger.warning("Não foi possível inicializar o banco: %s", exc)
    yield


app = FastAPI(
    title=settings.app_name,
    summary="Automação de prazos processuais a partir de e-mails de protocolo",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def protecao_basica(request: Request, call_next):
    """HTTP Basic opcional (``APP_PASSWORD``) para proteger dados jurídicos."""
    if not settings.app_password or request.url.path in ("/health",):
        return await call_next(request)

    cabecalho = request.headers.get("authorization", "")
    if not cabecalho.lower().startswith("basic "):
        return _desafio_basico()

    try:
        usuario_senha = base64.b64decode(cabecalho[6:]).decode("utf-8", "replace")
        _, _, senha = usuario_senha.partition(":")
    except Exception:
        return _desafio_basico()

    if not secrets.compare_digest(senha, settings.app_password):
        return _desafio_basico()

    return await call_next(request)


def _desafio_basico() -> Response:
    return JSONResponse(
        {"detail": "Autenticação necessária."},
        status_code=401,
        # Cabeçalhos HTTP devem ser ASCII: sem acento no realm.
        headers={"WWW-Authenticate": 'Basic realm="Produtividade Juridica"'},
    )


app.include_router(pages_router.router)
app.include_router(api_router.router)
app.include_router(sync_router.router)
