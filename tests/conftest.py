"""Fixtures compartilhadas.

O ``DATABASE_URL`` é definido **antes** de importar ``app`` porque a engine é
criada no import do módulo ``app.db``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="prazos-testes-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'testes.db'}"
os.environ["SYNC_TOKEN"] = "token-de-teste"
os.environ["APP_PASSWORD"] = ""
os.environ["GMAIL_CLIENT_ID"] = ""
os.environ["GMAIL_CLIENT_SECRET"] = ""
os.environ["GMAIL_REFRESH_TOKEN"] = ""
os.environ["DEEPSEEK_API_KEY"] = ""

from datetime import datetime  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Base, build_engine  # noqa: E402
from app.services.gmail import MensagemEmail, PaginaMensagens  # noqa: E402
from app.services.normalizacao import PrazoExtraido  # noqa: E402


@pytest.fixture()
def db():
    """Sessão com banco em memória, isolada por teste."""
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessao = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield sessao
    finally:
        sessao.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    """TestClient com o banco de teste injetado."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def settings_teste(**overrides) -> Settings:
    """Configuração determinística para testes."""
    base = {
        "database_url": "sqlite://",
        "sync_token": "token-de-teste",
        "max_segundos_por_execucao": 30,
        "gmail_max_mensagens_por_execucao": 50,
        "gmail_marcar_como_lida": True,
    }
    base.update(overrides)
    return Settings(**base)


class FakeGmail:
    """Substituto do ``GmailClient`` em testes."""

    def __init__(self, mensagens: list[MensagemEmail]) -> None:
        self.mensagens = {m.id: m for m in mensagens}
        self.lidas: list[str] = []
        self.consultas = 0
        self.falhar_em: set[str] = set()

    def listar_mensagens(self, query, *, max_results=25, page_token=None) -> PaginaMensagens:
        self.consultas += 1
        return PaginaMensagens(ids=list(self.mensagens)[:max_results])

    def obter_mensagem(self, message_id: str) -> MensagemEmail:
        if message_id in self.falhar_em:
            from app.services.gmail import GmailError

            raise GmailError("falha simulada")
        return self.mensagens[message_id]

    def marcar_como_lida(self, message_id: str) -> bool:
        self.lidas.append(message_id)
        return True


class FakeDeepSeek:
    """Substituto do ``DeepSeekClient`` em testes."""

    def __init__(self, respostas: dict[str, list[PrazoExtraido] | Exception]) -> None:
        self.respostas = respostas
        self.chamadas: list[str] = []
        self.processos_recebidos: list[list[str]] = []

    def extrair_prazos(
        self,
        *,
        assunto,
        remetente_nome,
        remetente_email,
        data_recebimento,
        corpo,
        processos_conhecidos=None,
    ):
        self.chamadas.append(assunto)
        self.processos_recebidos.append(list(processos_conhecidos or []))
        resposta = self.respostas.get(assunto)
        if isinstance(resposta, Exception):
            raise resposta
        return list(resposta or [])


def prazo_ok(
    advogado: str = "Dra. Maria Souza",
    tipo: str = "Manifestação",
    quando: str = "2026-11-20",
    processo: str = "0001234-56.2024.8.26.0100",
) -> PrazoExtraido:
    from datetime import date

    return PrazoExtraido(
        advogado=advogado,
        tipo_prazo=tipo,
        data_prazo=date.fromisoformat(quando),
        numero_processo=processo,
        status="OK",
    )


def mensagem(
    message_id: str = "MSG1",
    assunto: str = "Intimação",
    corpo: str = "Corpo do e-mail",
    remetente: tuple[str, str] = ("Secretaria TJSP", "protocolo@tjsp.jus.br"),
    quando: datetime | None = None,
) -> MensagemEmail:
    return MensagemEmail(
        id=message_id,
        thread_id=f"thread-{message_id}",
        remetente_nome=remetente[0],
        remetente_email=remetente[1],
        assunto=assunto,
        data_recebimento=quando or datetime(2026, 11, 5, 12, 0),
        corpo=corpo,
    )
