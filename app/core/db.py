"""Engine e sessões do banco de dados.

Funciona com SQLite (desenvolvimento local) e PostgreSQL (produção/Vercel)
apenas trocando a variável ``DATABASE_URL``.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base declarativa de todos os modelos."""


def build_engine(url: str) -> Engine:
    """Cria a engine adequada ao tipo de banco informado."""
    if url.startswith("sqlite"):
        # Banco em memória precisa de uma única conexão compartilhada
        # (o TestClient e o threadpool do FastAPI usam outras threads).
        if url in ("sqlite://", "sqlite:///:memory:") or ":memory:" in url:
            return create_engine(
                url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
                future=True,
            )

        caminho = Path(url.replace("sqlite:///", ""))
        if caminho.parent and str(caminho.parent) not in ("", "."):
            caminho.parent.mkdir(parents=True, exist_ok=True)

        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            future=True,
        )

    return create_engine(url, pool_pre_ping=True, pool_recycle=300, future=True)


settings = get_settings()
engine: Engine = build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Dependência do FastAPI: entrega uma sessão por requisição."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Cria as tabelas que ainda não existem e adiciona colunas novas."""
    # Importa os modelos de cada módulo para registrá-los no metadata.
    from app.modules.prazos import models  # noqa: F401

    Base.metadata.create_all(engine)
    garantir_colunas_novas()


#: Colunas adicionadas depois da versão inicial (bancos já existentes).
COLUNAS_NOVAS: dict[str, dict[str, str]] = {
    "emails": {
        "numero_processo": "VARCHAR(40) DEFAULT ''",
        "conta_gmail": "VARCHAR(64) DEFAULT ''",
    },
    "prazos": {
        "sheet_linha": "INTEGER",
        "sheet_sincronizado_em": "DATETIME",
        "atualizado_em": "DATETIME",
    },
}


def garantir_colunas_novas() -> None:
    """``ALTER TABLE ... ADD COLUMN`` para bancos criados antes de uma migração.

    Só adiciona colunas anuláveis/com default — compatível com SQLite e Postgres.
    Uma solução com Alembic está no roadmap; aqui o objetivo é nunca quebrar o
    deploy por causa de uma coluna nova.
    """
    inspetor = inspect(engine)

    for tabela, colunas in COLUNAS_NOVAS.items():
        if not inspetor.has_table(tabela):
            continue

        existentes = {coluna["name"] for coluna in inspetor.get_columns(tabela)}

        with engine.begin() as conexao:
            for nome, tipo in colunas.items():
                if nome in existentes:
                    continue
                conexao.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}"))
