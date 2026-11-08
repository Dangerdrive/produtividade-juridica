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

from app.core.config import get_settings
from app.core.db import init_db
from app.core.router import router as core_router
from app.core.templating import STATIC_DIR
from app.modules.google.router import router as google_router
from app.modules.prazos.routers.api import router as prazos_api_router
from app.modules.prazos.routers.pages import router as prazos_pages_router
from app.modules.prazos.routers.sync import router as prazos_sync_router
from app.modules.repasses.routers.api import router as repasses_api_router
from app.modules.repasses.routers.pages import router as repasses_pages_router

logger = logging.getLogger(__name__)
settings = get_settings()

#: Rotas que não exigem HTTP Basic: saúde, estáticos e links individuais de
#: cliente (protegidos por token próprio).
ROTAS_PUBLICAS = (
    "/health",
    "/static",
    "/static/",
    "/favicon.ico",
    "/repasses/cliente/",
    "/api/repasses/cliente/",
)


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
    summary="Plataforma modular de automação jurídica",
    version="2.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def protecao_basica(request: Request, call_next):
    """HTTP Basic opcional (``APP_PASSWORD``) para proteger dados jurídicos."""
    caminho = request.url.path
    if not settings.app_password or any(
        caminho == rota or (rota.endswith("/") and caminho.startswith(rota))
        for rota in ROTAS_PUBLICAS
    ):
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


app.include_router(core_router)
app.include_router(prazos_pages_router)
app.include_router(prazos_api_router)
app.include_router(prazos_sync_router)
app.include_router(google_router)
app.include_router(repasses_pages_router)
app.include_router(repasses_api_router)
