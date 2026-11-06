"""Modelos ORM.

Diferente da planilha do Apps Script, os dados são normalizados:

- ``emails``  -> uma linha por mensagem do Gmail (ingestão determinística, sem IA)
- ``prazos``  -> uma linha por prazo extraído pela IA, ligada ao e-mail

Assim o caso "um e-mail com três prazos" não precisa mais de linhas duplicadas.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    """Agora em UTC, sem tzinfo (compatível com SQLite e Postgres)."""
    return datetime.now(UTC).replace(tzinfo=None)


class StatusExtracao(str, enum.Enum):
    """Máquina de estados do processamento de um e-mail."""

    PENDENTE_IA = "PENDENTE_IA"
    OK = "OK"
    REVISAR = "REVISAR"
    SEM_PRAZO = "SEM_PRAZO"
    ERRO_IA = "ERRO_IA"
    ERRO_JSON = "ERRO_JSON"


#: Status considerados finais — não são reprocessados.
STATUS_FINAIS: frozenset[str] = frozenset(
    {StatusExtracao.OK.value, StatusExtracao.REVISAR.value, StatusExtracao.SEM_PRAZO.value}
)

#: Status que permitem reprocessar a mesma mensagem.
STATUS_REPROCESSAVEIS: frozenset[str] = frozenset(
    {
        StatusExtracao.PENDENTE_IA.value,
        StatusExtracao.ERRO_IA.value,
        StatusExtracao.ERRO_JSON.value,
    }
)


class Email(Base):
    """Mensagem do Gmail.

    As colunas de ingestão (remetente, assunto, data) são preenchidas **antes**
    da chamada ao modelo, para que uma falha de IA nunca perca o dado original.
    """

    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), index=True)

    remetente_nome: Mapped[str] = mapped_column(String(255), default="")
    remetente_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    assunto: Mapped[str] = mapped_column(Text, default="")
    data_recebimento: Mapped[datetime] = mapped_column(DateTime(), index=True)

    #: Número CNJ extraído do ASSUNTO por regex (sem IA), antes da chamada ao modelo.
    numero_processo: Mapped[str] = mapped_column(String(40), default="", index=True)

    status: Mapped[str] = mapped_column(
        String(20), default=StatusExtracao.PENDENTE_IA.value, index=True
    )
    erro: Mapped[str | None] = mapped_column(Text, default=None)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)

    criado_em: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    processado_em: Mapped[datetime | None] = mapped_column(DateTime(), default=None)

    prazos: Mapped[list[Prazo]] = relationship(
        back_populates="email",
        cascade="all, delete-orphan",
        order_by="Prazo.id",
    )

    def __repr__(self) -> str:  # pragma: no cover - depuração
        return f"<Email {self.gmail_message_id} status={self.status}>"


class Prazo(Base):
    """Prazo processual extraído pela IA."""

    __tablename__ = "prazos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), index=True
    )

    advogado: Mapped[str] = mapped_column(String(255), default="", index=True)
    tipo_prazo: Mapped[str] = mapped_column(String(120), default="", index=True)
    data_prazo: Mapped[date | None] = mapped_column(Date(), default=None, index=True)
    numero_processo: Mapped[str] = mapped_column(String(40), default="", index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=StatusExtracao.OK.value, index=True
    )

    # --- Espelhamento no Google Sheets ---------------------------------
    #: Linha ocupada na aba DB_Prazos (``None`` = ainda não enviado).
    sheet_linha: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    #: Última vez que a linha da planilha foi enviada/atualizada.
    sheet_sincronizado_em: Mapped[datetime | None] = mapped_column(DateTime(), default=None)
    #: Última alteração de conteúdo (usada para detectar o que reenviar).
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)

    email: Mapped[Email] = relationship(back_populates="prazos")

    def __repr__(self) -> str:  # pragma: no cover - depuração
        return f"<Prazo {self.numero_processo} {self.tipo_prazo} {self.data_prazo}>"


class SyncState(Base):
    """Estado de sincronização (checkpoint de paginação do Gmail)."""

    __tablename__ = "sync_state"

    chave: Mapped[str] = mapped_column(String(64), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(), default=utcnow, onupdate=utcnow
    )


Index("ix_prazos_advogado_tipo", Prazo.advogado, Prazo.tipo_prazo)
